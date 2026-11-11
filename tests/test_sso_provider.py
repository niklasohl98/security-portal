from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.sso_provider import EntraGrant, EntraProvider, SsoError, validate_id_token
from tests.test_sso import ADMIN, CFG, CLIENT, GROUP, OID, PUBLIC, TENANT


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _claims(**overrides: object) -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": CFG.issuer,
        "aud": CLIENT,
        "tid": TENANT,
        "oid": OID,
        "sub": "test-subject",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "nonce": hashlib.sha256(b"nonce").hexdigest(),
        **overrides,
    }


def _jwks(key: rsa.RSAPrivateKey) -> dict[str, Any]:
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": "test-key"}]}


def _token(key: rsa.RSAPrivateKey, **claims: object) -> str:
    return jwt.encode(_claims(**claims), key, algorithm="RS256", headers={"kid": "test-key"})


def test_signed_id_token_is_bound_to_tenant_application_and_nonce(key: rsa.RSAPrivateKey) -> None:
    assert validate_id_token(_token(key), _jwks(key), CFG, "nonce") == OID


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "foreign-app"},
        {"aud": [CLIENT, "foreign-app"]},
        {"iss": "https://foreign.example"},
        {"tid": CLIENT},
        {"nonce": "foreign-nonce"},
        {"nonce": 3},
        {"exp": 0},
        {"nbf": 9999999999},
        {"iat": 9999999999},
        {"oid": "not-guid"},
        {"sub": None},
    ],
)
def test_wrong_signed_claims_are_rejected(
    key: rsa.RSAPrivateKey, claims: dict[str, object]
) -> None:
    token = _token(key, **claims)
    with pytest.raises(SsoError, match="token") as exc:
        validate_id_token(token, _jwks(key), CFG, "nonce")
    assert token not in str(exc.value)


def test_bad_signature_algorithm_key_and_missing_claim_are_rejected(key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = _claims()
    del claims["exp"]
    missing = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    unsigned = jwt.encode(_claims(), "", algorithm="none", headers={"kid": "test-key"})
    for token, jwks in (
        (missing, _jwks(key)),
        (unsigned, _jwks(key)),
        (_token(other), _jwks(key)),
        (_token(key), {"keys": []}),
        ("not-a-jwt", _jwks(key)),
        (_token(key), {"keys": [None]}),
    ):
        with pytest.raises(SsoError, match="token"):
            validate_id_token(token, jwks, CFG, "nonce")


@pytest.fixture
def provider(tmp_path: Path, key: rsa.RSAPrivateKey, respx_mock: respx.MockRouter) -> EntraProvider:
    (tmp_path / CFG.certificate_secret).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    base = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
    metadata = {
        "authorization_endpoint": base + "/authorize",
        "token_endpoint": base + "/token",
        "issuer": CFG.issuer,
        "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
    }
    respx_mock.get(
        re.compile(r"https://login.microsoftonline.com/.*/.well-known/openid-configuration")
    ).respond(json=metadata)
    respx_mock.get(f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys").respond(
        json=_jwks(key)
    )
    return EntraProvider(CFG, tmp_path, PUBLIC)


def _complete(provider: EntraProvider, key: rsa.RSAPrivateKey, mock: respx.MockRouter) -> Any:
    flow = provider.begin()
    token = _token(key, nonce=hashlib.sha256(flow["nonce"].encode()).hexdigest())
    mock.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token").respond(
        json={
            "access_token": "synthetic-access-token",
            "id_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "User.Read",
            "client_info": "eyJ1aWQiOiJ0ZXN0IiwidXRpZCI6InRlc3QifQ",
        }
    )
    return provider.complete(flow, {"state": flow["state"], "code": "synthetic-code"})


@pytest.mark.respx(assert_all_called=False)
def test_real_msal_flow_enforces_state_pkce_jwt_and_group_paging(
    provider: EntraProvider,
    key: rsa.RSAPrivateKey,
    respx_mock: respx.MockRouter,
) -> None:
    flow = provider.begin()
    with pytest.raises(SsoError, match="auth"):
        provider.complete(flow, {"state": "foreign", "code": "synthetic-code"})
    grant = _complete(provider, key, respx_mock)
    assert grant.object_id == OID
    first = respx_mock.get(
        "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id"
    ).respond(
        json={
            "value": [{"id": GROUP}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$skiptoken=next",
        }
    )
    respx_mock.get(
        "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$skiptoken=next"
    ).respond(json={"value": [{"id": ADMIN}]})
    assert provider.groups(grant) == {GROUP, ADMIN}
    assert first.calls[0].request.headers["authorization"] == "Bearer synthetic-access-token"
    token_request = next(call.request for call in respx_mock.calls if call.request.method == "POST")
    assert b"code_verifier=" in token_request.content
    form = parse_qs(token_request.content.decode("ascii"))
    assertion = form["client_assertion"][0]
    assert assertion.count(".") == 2
    assert not assertion.startswith("b'")
    provider.close()


@pytest.mark.respx(assert_all_called=False)
def test_graph_failure_and_foreign_paging_are_fail_closed(
    provider: EntraProvider,
    key: rsa.RSAPrivateKey,
    respx_mock: respx.MockRouter,
) -> None:
    grant = _complete(provider, key, respx_mock)
    route = respx_mock.get("https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id")
    for response in (
        httpx.Response(403),
        httpx.Response(200, json={"value": [], "@odata.nextLink": "https://foreign.example/"}),
        httpx.Response(200, json={"value": [{"id": "bad"}]}),
    ):
        route.mock(return_value=response)
        with pytest.raises(SsoError, match="groups"):
            provider.groups(grant)
    provider.close()


def test_missing_certificate_does_not_echo_secret_name_or_content(tmp_path: Path) -> None:
    with pytest.raises(SsoError, match="configuration"):
        EntraProvider(CFG, tmp_path, PUBLIC)


@pytest.mark.respx(assert_all_called=False)
def test_user_denial_and_library_failure_never_expose_identity_provider_details(
    provider: EntraProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = provider.begin()
    with pytest.raises(SsoError, match="auth") as error:
        provider.complete(
            flow,
            {
                "state": flow["state"],
                "error": "access_denied",
                "error_description": "synthetic-sensitive-message",
            },
        )
    assert "synthetic-sensitive-message" not in str(error.value)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic-sensitive-message")

    monkeypatch.setattr(provider.application, "initiate_auth_code_flow", broken)
    with pytest.raises(SsoError, match="auth"):
        provider.begin()
    provider.close()


@pytest.mark.respx(assert_all_called=False)
def test_token_exchange_logs_only_numeric_entra_code(
    provider: EntraProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = provider.begin()
    monkeypatch.setattr(
        provider.application,
        "acquire_token_by_auth_code_flow",
        lambda *_args, **_kwargs: {
            "error": "invalid_client",
            "error_codes": [700027],
            "error_description": "synthetic-sensitive-message",
        },
    )
    with pytest.raises(SsoError, match=r"^auth_700027$") as error:
        provider.complete(flow, {"state": flow["state"], "code": "synthetic-code"})
    assert "synthetic-sensitive-message" not in str(error.value)


@pytest.mark.respx(assert_all_called=False)
def test_revoked_refresh_token_and_missing_accounts_do_not_authorize(
    provider: EntraProvider,
) -> None:
    class ExpiredAccount:
        def get_accounts(self) -> list[dict[str, str]]:
            return [{}]

        def acquire_token_silent(self, *_args: object, **_kwargs: object) -> None:
            return None

    class MissingAccount(ExpiredAccount):
        def get_accounts(self) -> list[dict[str, str]]:
            return []

    with pytest.raises(SsoError, match="cache"):
        provider.groups(EntraGrant(OID, MissingAccount()))
    with pytest.raises(SsoError, match="refresh"):
        provider.groups(EntraGrant(OID, ExpiredAccount()))
    provider.close()


@pytest.mark.respx(assert_all_called=False)
def test_cyclic_graph_paging_is_bounded_and_not_treated_as_complete(
    provider: EntraProvider,
    key: rsa.RSAPrivateKey,
    respx_mock: respx.MockRouter,
) -> None:
    grant = _complete(provider, key, respx_mock)
    route = respx_mock.get(
        "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id"
    ).respond(
        json={
            "value": [{"id": GROUP}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id",
        }
    )
    with pytest.raises(SsoError, match="groups"):
        provider.groups(grant)
    assert len(route.calls) == 100
    provider.close()
