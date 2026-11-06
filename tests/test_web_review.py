from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import AuditLog, Identity, Tenant
from app.main import create_app
from tests.identity_fakes import ago, get_tenant

XSS = "<script>alert(1)</script>"


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def _session(client: TestClient) -> Session:
    session: Session = cast(FastAPI, client.app).state.session_factory()
    return session


def _add_missing(client: TestClient, slug: str, name: str, **kw: object) -> int:
    with _session(client) as s, s.begin():
        row = Identity(
            tenant_id=get_tenant(s, slug).id,
            key=f"{slug}-{name}",
            display_name=name,
            account_type="member_cloud",
            status="missing",
            missing_since=ago(5),
            last_login_at=ago(40),
            **kw,
        )
        s.add(row)
        s.flush()
        return row.id


def _post(
    client: TestClient, path: str, decision: str = "keep", headers: dict[str, str] | None = None
) -> httpx.Response:
    response: httpx.Response = client.post(
        path,
        content=f"decision={decision}",
        headers={"content-type": "application/x-www-form-urlencoded", **(headers or {})},
        follow_redirects=False,
    )
    return response


def _identity(client: TestClient, identity_id: int) -> Identity | None:
    with _session(client) as s:
        return s.get(Identity, identity_id)


def test_page_lists_open_items_per_tenant_and_escapes_names(client: TestClient) -> None:
    _add_missing(client, "cg", XSS)
    _add_missing(client, "cvh", "Nur CVH")
    r = client.get("/review")
    assert r.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert XSS not in r.text
    assert "Nur CVH" in r.text
    assert "Verschwunden seit" in r.text
    # missing_since = 01.11.2026 12:00 UTC, angezeigt in Europe/Berlin (Winterzeit)
    assert "01.11.2026 13:00" in r.text


def test_page_without_items_says_nothing_to_review(client: TestClient) -> None:
    r = client.get("/review")
    assert r.status_code == 200
    assert r.text.count("Nichts zu prüfen.") == 2  # cg und cvh


def test_disabled_tenants_are_not_listed(client: TestClient) -> None:
    _add_missing(client, "cvh", "Verborgen")
    with _session(client) as s, s.begin():
        s.scalars(select(Tenant).where(Tenant.slug == "cvh")).one().enabled = False
    assert "Verborgen" not in client.get("/review").text


def test_security_headers_and_no_store_on_html(client: TestClient) -> None:
    r = client.get("/review")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "script-src" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    # "no-referrer" ließe Browser bei Formularen "Origin: null" senden; die CSRF-Prüfung
    # lehnte dann die eigene Seite ab. "same-origin" behält den Origin und sendet nach außen nichts.
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"


def test_stylesheet_is_served(client: TestClient) -> None:
    r = client.get("/static/style.css")
    assert (r.status_code, r.headers["content-type"].split(";")[0]) == (200, "text/css")


def test_message_comes_from_a_fixed_list_only(client: TestClient) -> None:
    assert "Eintrag wird behalten." in client.get("/review?done=keep").text
    r = client.get(f"/review?done={XSS}")
    assert "<script>" not in r.text
    assert 'role="status"' not in r.text  # unbekannter Wert: keine Meldung


def test_keep_decision_then_it_moves_to_the_kept_list(client: TestClient) -> None:
    item = _add_missing(client, "cg", "Anna")
    r = _post(client, f"/review/cg/{item}", "keep")
    assert (r.status_code, r.headers["location"]) == (303, "/review?done=keep")
    page = client.get("/review").text
    assert "Behalten (1)" in page
    assert 'value="reopen"' in page
    with _session(client) as s:
        (entry,) = s.scalars(select(AuditLog)).all()
        assert (entry.action, entry.actor) == ("identity.keep", "lokal (ohne Anmeldung)")


def test_delete_needs_a_confirmation_page_and_then_removes_the_item(client: TestClient) -> None:
    item = _add_missing(client, "cg", "Berta")
    confirm = client.get(f"/review/cg/{item}/delete")
    assert confirm.status_code == 200
    assert "Berta" in confirm.text
    assert "nicht verändert" in confirm.text
    assert _identity(client, item) is not None  # die Seite selbst ändert nichts
    r = _post(client, f"/review/cg/{item}", "delete")
    assert r.headers["location"] == "/review?done=delete"
    assert _identity(client, item) is None


def test_reopen_returns_a_kept_item_to_the_open_list(client: TestClient) -> None:
    item = _add_missing(client, "cg", "Clara", review_decision="keep", reviewed_at=ago(1))
    _post(client, f"/review/cg/{item}", "reopen")
    assert "Behalten (" not in client.get("/review").text
    row = _identity(client, item)
    assert row is not None
    assert row.review_decision is None


def test_decisions_cannot_cross_tenants(client: TestClient) -> None:
    foreign = _add_missing(client, "cvh", "Fremd")
    r = _post(client, f"/review/cg/{foreign}", "delete")  # richtige ID, falscher Mandant
    assert r.headers["location"] == "/review?done=error"
    assert client.get(f"/review/cg/{foreign}/delete").status_code == 404
    assert _identity(client, foreign) is not None
    with _session(client) as s:
        assert s.scalars(select(AuditLog)).all() == []


def test_invalid_requests_are_rejected(client: TestClient) -> None:
    item = _add_missing(client, "cg", "Dora")
    assert _post(client, f"/review/cg/{item}", "zerstoeren").status_code == 400
    assert _post(client, f"/review/gibtsnicht/{item}", "keep").status_code == 404
    assert client.get("/review/cg/999/delete").status_code == 404
    plain = client.post(f"/review/cg/{item}", json={"decision": "keep"}, follow_redirects=False)
    assert plain.status_code == 415
    big = client.post(
        f"/review/cg/{item}",
        content="decision=keep&x=" + "a" * 5000,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert big.status_code == 413
    bad = client.post(
        f"/review/cg/{item}",
        content=b"decision=\xff",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert bad.status_code == 400
    row = _identity(client, item)
    assert row is not None
    assert row.review_decision is None


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://boese.example"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ],
)
def test_cross_site_posts_are_refused(client: TestClient, headers: dict[str, str]) -> None:
    item = _add_missing(client, "cg", "Emil")
    r = _post(client, f"/review/cg/{item}", "delete", headers=headers)
    assert r.status_code == 403
    assert _identity(client, item) is not None


@pytest.mark.parametrize(
    "headers",
    [{"origin": "http://testserver"}, {"sec-fetch-site": "same-origin"}, {}],
)
def test_same_origin_and_script_posts_are_accepted(
    client: TestClient, headers: dict[str, str]
) -> None:
    item = _add_missing(client, "cg", "Finn")
    r = _post(client, f"/review/cg/{item}", "keep", headers=headers)
    assert r.status_code == 303
