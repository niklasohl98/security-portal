from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.connectors.check import check_entra
from app.connectors.graph import (
    GraphClient,
    GraphError,
    MsalCertificateTokenProvider,
    fetch_organization,
    fetch_skus,
    fetch_users,
    parse_graph_datetime,
    parse_user,
)
from tests.graph_fakes import BASE, FakeTokens, load, make_users

USERS = f"{BASE}/users"


def _client(sleeps: list[float] | None = None, **kw: object) -> GraphClient:
    rec = sleeps if sleeps is not None else []
    return GraphClient(FakeTokens(), sleep=rec.append, **kw)  # type: ignore[arg-type]


@respx.mock
def test_paging_follows_next_link_over_two_pages() -> None:
    users = make_users(651)
    next_link = f"{USERS}?$skiptoken=abc"
    respx.get(USERS, params={"$skiptoken": "abc"}).mock(
        return_value=httpx.Response(200, json={"value": users[400:]})
    )
    first = respx.get(USERS).mock(
        return_value=httpx.Response(200, json={"value": users[:400], "@odata.nextLink": next_link})
    )
    result = fetch_users(_client())
    assert len(result.users) == 651
    assert result.sign_in_available is True
    assert first.call_count == 1


@respx.mock
def test_first_request_selects_expected_fields_and_page_size() -> None:
    route = respx.get(USERS).mock(return_value=httpx.Response(200, json={"value": []}))
    fetch_users(_client())
    q = route.calls[0].request.url.params
    assert "signInActivity" in q["$select"]
    assert "licenseAssignmentStates" in q["$select"]
    assert q["$top"] == "500"  # mit signInActivity max. 500 (Graph-Doku)
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"


@respx.mock
def test_duplicates_across_pages_are_dropped() -> None:
    page = make_users(3)
    respx.get(USERS, params={"$skiptoken": "x"}).mock(
        return_value=httpx.Response(200, json={"value": page[2:]})
    )
    respx.get(USERS).mock(
        return_value=httpx.Response(
            200, json={"value": page, "@odata.nextLink": f"{USERS}?$skiptoken=x"}
        )
    )
    assert len(fetch_users(_client()).users) == 3


@respx.mock
@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example.invalid/v1.0/users?$skiptoken=1",
        "http://graph.microsoft.com/v1.0/users?$skiptoken=1",
        "https://graph.microsoft.com.evil.example.invalid/v1.0/users",
        "https://graph.microsoft.com:8443/v1.0/users",
        "https://user@evil.example.invalid/",
    ],
)
def test_foreign_next_link_is_refused_and_token_not_sent(link: str) -> None:
    respx.get(USERS).mock(
        return_value=httpx.Response(200, json={"value": [], "@odata.nextLink": link})
    )
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert exc.value.code == "invalid_next_link"
    assert len(respx.calls) == 1  # der fremde Link wurde nie aufgerufen


@respx.mock
def test_retry_after_is_honored_on_429() -> None:
    route = respx.get(USERS).mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": "7"}, json={"error": {"code": "TooManyRequests"}}
            ),
            httpx.Response(200, json={"value": []}),
        ]
    )
    sleeps: list[float] = []
    fetch_users(_client(sleeps))
    assert sleeps == [7.0]
    assert route.call_count == 2


@respx.mock
def test_exponential_backoff_without_retry_after_and_cap() -> None:
    respx.get(USERS).mock(
        side_effect=[httpx.Response(503)] * 4 + [httpx.Response(200, json={"value": []})]
    )
    sleeps: list[float] = []
    fetch_users(_client(sleeps, backoff_base=10.0, backoff_cap=25.0))
    assert sleeps == [10.0, 20.0, 25.0, 25.0]


@respx.mock
def test_huge_retry_after_is_capped() -> None:
    respx.get(USERS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "99999"}),
            httpx.Response(200, json={"value": []}),
        ]
    )
    sleeps: list[float] = []
    fetch_users(_client(sleeps))
    assert sleeps == [300.0]


@respx.mock
def test_gives_up_after_max_retries_with_status_and_code_only() -> None:
    respx.get(USERS).mock(
        return_value=httpx.Response(
            503, json={"error": {"code": "ServiceUnavailable", "message": "max.muster@x.invalid"}}
        )
    )
    sleeps: list[float] = []
    with pytest.raises(GraphError) as exc:
        fetch_users(_client(sleeps, max_retries=2))
    assert len(sleeps) == 2
    assert (exc.value.status, exc.value.code) == (503, "ServiceUnavailable")
    assert "max.muster" not in str(exc.value)


@respx.mock
def test_network_error_is_retried_then_reported_without_details() -> None:
    respx.get(USERS).mock(side_effect=httpx.ConnectError("host=intern.example.invalid"))
    with pytest.raises(GraphError) as exc:
        fetch_users(_client(max_retries=1))
    assert exc.value.code == "network:ConnectError"
    assert "intern" not in str(exc.value)


@respx.mock
def test_non_retryable_error_is_raised_immediately() -> None:
    route = respx.get(USERS).mock(
        return_value=httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
    )
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert exc.value.status == 401
    assert route.call_count == 1


@respx.mock
def test_missing_p1_falls_back_without_sign_in_activity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "signInActivity" in request.url.params["$select"]:
            return httpx.Response(403, json={"error": {"code": "Forbidden"}})
        return httpx.Response(200, json={"value": make_users(2)})

    route = respx.get(USERS).mock(side_effect=handler)
    result = fetch_users(_client())
    assert result.sign_in_available is False
    assert len(result.users) == 2
    assert route.calls[1].request.url.params["$top"] == "999"
    assert all(u.last_successful_sign_in is None for u in result.users)


@respx.mock
def test_403_without_sign_in_activity_is_a_real_permission_error() -> None:
    respx.get(USERS).mock(
        return_value=httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied"}})
    )
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert exc.value.status == 403


def test_parse_fixture_users() -> None:
    users = {u.display_name: u for u in map(parse_user, load("graph_users")["value"])}
    erika = users["Erika Beispiel"]
    assert erika.last_successful_sign_in == datetime(2026, 10, 31, 9, 30, tzinfo=UTC)
    assert erika.last_sign_in == datetime(2026, 10, 30, 7, 0, tzinfo=UTC)
    assert erika.on_prem_immutable_id is not None
    assert erika.licenses == [
        {
            "sku_id": "00000000-0000-4000-8000-0000000000e3",
            "direct": True,
            "group": False,
            "state": "Active",
        }
    ]
    max_ = users["Max Muster"]
    assert max_.last_successful_sign_in is None
    assert max_.licenses == []
    assert users["Gast Beispiel"].user_type == "Guest"
    assert users["Dieter Deaktiviert"].account_enabled is False


def test_licenses_group_direct_and_worst_state() -> None:
    gruppe = parse_user(load("graph_users")["value"][3])
    by_sku = {x["sku_id"][-2:]: x for x in gruppe.licenses}
    assert by_sku["e3"]["direct"] is True
    assert by_sku["e3"]["group"] is True  # beides gleichzeitig
    assert by_sku["ee"]["state"] == "ActiveWithError"
    # 7 Nachkommastellen von Graph werden akzeptiert
    assert gruppe.last_successful_sign_in is not None
    assert gruppe.last_successful_sign_in.tzinfo is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2026-01-02T03:04:05+01:00", datetime(2026, 1, 2, 2, 4, 5, tzinfo=UTC)),
        ("2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("kaputt", None),
        ("", None),
        (None, None),
        (12345, None),
    ],
)
def test_parse_graph_datetime(raw: object, expected: datetime | None) -> None:
    assert parse_graph_datetime(raw) == expected


@respx.mock
def test_fetch_skus_and_organization() -> None:
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )
    respx.get(f"{BASE}/organization").mock(
        return_value=httpx.Response(200, json=load("graph_organization"))
    )
    skus = fetch_skus(_client())
    assert [s.part_number for s in skus] == ["SPE_E3", "EXCHANGEENTERPRISE", "FLOW_FREE"]
    assert skus[0].enabled_units == 10
    assert skus[0].consumed_units == 2
    org = fetch_organization(_client())
    assert org.display_name == "Beispiel GmbH"
    assert len(org.domains) == 2


@respx.mock
def test_fetch_organization_empty() -> None:
    respx.get(f"{BASE}/organization").mock(return_value=httpx.Response(200, json={"value": []}))
    assert fetch_organization(_client()).display_name is None


@respx.mock
def test_check_entra_reports_missing_sign_in_activity() -> None:
    respx.get(f"{BASE}/organization").mock(
        return_value=httpx.Response(200, json=load("graph_organization"))
    )
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )
    respx.get(USERS).mock(return_value=httpx.Response(403, json={"error": {"code": "Forbidden"}}))
    result = check_entra(_client())
    assert (result.sku_count, result.sign_in_available) == (3, False)


@respx.mock
def test_check_entra_raises_on_other_errors() -> None:
    respx.get(f"{BASE}/organization").mock(
        return_value=httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
    )
    with pytest.raises(GraphError):
        check_entra(_client())


class _FakeApp:
    created: dict[str, object] = {}  # noqa: RUF012

    def __init__(self, client_id: str, **kwargs: object) -> None:
        _FakeApp.created = {"client_id": client_id, **kwargs}

    def acquire_token_for_client(self, scopes: list[str]) -> dict[str, str]:
        assert scopes == ["https://graph.microsoft.com/.default"]
        return {"access_token": "tok"}


def test_msal_provider_uses_certificate_and_tenant_authority() -> None:
    provider = MsalCertificateTokenProvider(
        "tenant-guid", "client-guid", "PEM", "ABCDEF", app_factory=_FakeApp
    )
    assert provider.get_token() == "tok"
    assert _FakeApp.created["authority"] == "https://login.microsoftonline.com/tenant-guid"
    assert _FakeApp.created["client_credential"] == {"private_key": "PEM", "thumbprint": "ABCDEF"}


def test_msal_provider_drops_error_description() -> None:
    class Failing(_FakeApp):
        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, str]:
            return {
                "error": "invalid_client",
                "error_description": "AADSTS700027 geheim-zertifikat",
            }

    provider = MsalCertificateTokenProvider("t", "c", "PEM", "AB", app_factory=Failing)
    with pytest.raises(GraphError) as exc:
        provider.get_token()
    assert exc.value.code == "auth:invalid_client"
    assert "geheim" not in str(exc.value)


@respx.mock
def test_error_body_that_is_not_json_still_gives_status() -> None:
    respx.get(USERS).mock(return_value=httpx.Response(400, text="<html>kaputt</html>"))
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert (exc.value.status, exc.value.code) == (400, None)


@respx.mock
def test_retry_after_as_http_date_falls_back_to_backoff() -> None:
    respx.get(USERS).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            httpx.Response(200, json={"value": []}),
        ]
    )
    sleeps: list[float] = []
    fetch_users(_client(sleeps, backoff_base=3.0))
    assert sleeps == [3.0]


@respx.mock
@pytest.mark.parametrize("body", [["kein", "objekt"], {"value": "keine liste"}])
def test_unexpected_body_shape_is_rejected(body: object) -> None:
    respx.get(USERS).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert exc.value.code == "invalid_body"


@respx.mock
def test_non_string_next_link_is_rejected() -> None:
    respx.get(USERS).mock(
        return_value=httpx.Response(200, json={"value": [], "@odata.nextLink": 5})
    )
    with pytest.raises(GraphError) as exc:
        fetch_users(_client())
    assert exc.value.code == "invalid_next_link"


def test_msal_provider_without_error_code() -> None:
    class Odd(_FakeApp):
        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, str]:
            return {}

    with pytest.raises(GraphError) as exc:
        MsalCertificateTokenProvider("t", "c", "PEM", "AB", app_factory=Odd).get_token()
    assert exc.value.code == "auth"
