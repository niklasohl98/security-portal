from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AccountException, AuditLog, Identity
from tests.identity_fakes import NOW, get_tenant, seed_world
from tests.web_fakes import identity_id, open_session, post_form

XSS = "<script>alert(1)</script>"


@pytest.fixture
def world(site: TestClient) -> TestClient:
    with open_session(site) as s, s.begin():
        seed_world(s)
    return site


def _identity(client: TestClient, name: str) -> Identity:
    with open_session(client) as s:
        return s.get_one(Identity, identity_id(client, name))


def _exception_path(client: TestClient, name: str) -> str:
    return f"/accounts/cg/{identity_id(client, name)}/exception"


def _until(days: int) -> str:
    return (NOW + timedelta(days=days)).date().isoformat()


def test_creating_an_exception_unflags_the_account_and_is_audited(world: TestClient) -> None:
    assert _identity(world, "Person 1").flagged is True
    r = post_form(
        world, _exception_path(world, "Person 1"), reason="Break-Glass", valid_until=_until(100)
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("?done=exception")
    row = _identity(world, "Person 1")
    assert (row.status, row.flagged) == ("exception", False)
    with open_session(world) as s:
        (ex,) = s.scalars(select(AccountException)).all()
        assert (ex.reason, ex.created_by) == ("Break-Glass", "lokal (ohne Anmeldung)")
        # gilt bis zum Ende des gewählten Tages (Ortszeit), nicht bis 00:00
        assert ex.valid_until.astimezone().date() >= (NOW + timedelta(days=100)).date()
        assert [a.action for a in s.scalars(select(AuditLog))] == ["exception.create"]


def test_the_detail_page_then_shows_and_removes_the_exception(world: TestClient) -> None:
    path = _exception_path(world, "Person 1")
    post_form(world, path, reason="Break-Glass", valid_until=_until(100))
    detail = world.get(path.removesuffix("/exception")).text
    assert "Break-Glass" in detail
    assert "Es gibt bereits eine gültige Ausnahme" in detail
    with open_session(world) as s:
        ex_id = s.scalars(select(AccountException.id)).one()
    r = post_form(world, f"/exceptions/cg/{ex_id}/delete")
    assert r.headers["location"] == "/exceptions?done=deleted"
    assert _identity(world, "Person 1").flagged is True  # wieder nach den Regeln bewertet


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"reason": "", "valid_until": "2027-01-01"}, "reason_required"),
        ({"reason": "x", "valid_until": "kein-datum"}, "bad_date"),
        ({"reason": "x", "valid_until": ""}, "bad_date"),
        ({"reason": "x", "valid_until": "2020-01-01"}, "not_in_future"),
        ({"reason": "x" * 501, "valid_until": "2027-01-01"}, "reason_too_long"),
    ],
)
def test_invalid_exceptions_redirect_with_a_fixed_error_code(
    world: TestClient, fields: dict[str, Any], code: str
) -> None:
    r = post_form(world, _exception_path(world, "Person 1"), **fields)
    assert r.headers["location"].endswith(f"?err={code}")
    with open_session(world) as s:
        assert s.scalars(select(AccountException)).all() == []


def test_second_valid_exception_is_refused(world: TestClient) -> None:
    path = _exception_path(world, "Person 1")
    post_form(world, path, reason="a", valid_until=_until(10))
    r = post_form(world, path, reason="b", valid_until=_until(20))
    assert r.headers["location"].endswith("?err=already_valid")


def test_exception_reason_is_escaped_on_every_page(world: TestClient) -> None:
    post_form(
        world,
        _exception_path(world, "Person 1"),
        reason="%3Cscript%3Ealert(1)%3C/script%3E",
        valid_until=_until(30),
    )
    for path in ("/exceptions", f"/accounts/cg/{identity_id(world, 'Person 1')}"):
        text = world.get(path).text
        assert XSS not in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_exceptions_page_highlights_soon_and_expired(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        t = get_tenant(s)
        for name, days in (("Person 1", 10), ("Person 2", -2), ("Person 3", 200)):
            s.add(
                AccountException(
                    tenant_id=t.id,
                    identity_id=identity_id(world, name),
                    reason="x",
                    created_by="t",
                    valid_until=NOW + timedelta(days=days),
                )
            )
    text = world.get("/exceptions").text
    assert text.count('class="soon"') == 1
    assert text.count('class="expired"') == 1
    assert "läuft bald ab" in text
    assert "abgelaufen" in text


def test_deleting_an_unknown_or_foreign_exception_changes_nothing(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        cvh = get_tenant(s, "cvh")
        other = Identity(tenant_id=cvh.id, key="x", display_name="Fremd")
        s.add(other)
        s.flush()
        ex = AccountException(
            tenant_id=cvh.id,
            identity_id=other.id,
            reason="x",
            created_by="t",
            valid_until=NOW + timedelta(days=5),
        )
        s.add(ex)
        s.flush()
        foreign_id = ex.id
    assert post_form(world, f"/exceptions/cg/{foreign_id}/delete").headers["location"] == (
        "/exceptions?done=error"
    )
    assert post_form(world, "/exceptions/cg/99999/delete").headers["location"].endswith("error")
    assert post_form(world, "/exceptions/gibtsnicht/1/delete").status_code == 404
    with open_session(world) as s:
        assert len(s.scalars(select(AccountException)).all()) == 1


def test_manual_account_type_is_saved_shown_and_can_be_reset(world: TestClient) -> None:
    path = f"/accounts/cg/{identity_id(world, 'Person 3')}"
    r = post_form(world, f"{path}/type", account_type="service")
    assert r.headers["location"].endswith("?done=type")
    detail = world.get(path).text
    assert "Dienst/Sync" in detail
    assert 'class="badge">manuell' in detail
    assert _identity(world, "Person 3").account_type_override == "service"
    post_form(world, f"{path}/type", account_type="")
    assert _identity(world, "Person 3").account_type_override is None
    assert 'class="badge">manuell' not in world.get(path).text


def test_invalid_account_type_is_refused(world: TestClient) -> None:
    path = f"/accounts/cg/{identity_id(world, 'Person 3')}"
    assert (
        post_form(world, f"{path}/type", account_type="zerstoeren")
        .headers["location"]
        .endswith("?err=bad_type")
    )
    assert (
        post_form(world, "/accounts/cg/99999/type", account_type="test")
        .headers["location"]
        .endswith("?err=not_found")
    )
    assert _identity(world, "Person 3").account_type_override is None


@pytest.mark.parametrize(
    "headers",
    [{"origin": "https://boese.example"}, {"origin": "null"}, {"sec-fetch-site": "cross-site"}],
)
def test_every_changing_route_refuses_cross_site_posts(
    world: TestClient, headers: dict[str, str]
) -> None:
    pid = identity_id(world, "Person 1")
    for path, fields in (
        (f"/accounts/cg/{pid}/exception", {"reason": "x", "valid_until": _until(9)}),
        (f"/accounts/cg/{pid}/type", {"account_type": "test"}),
        ("/exceptions/cg/1/delete", {}),
    ):
        assert post_form(world, path, headers=headers, **fields).status_code == 403
    assert world.post("/runs/cg/start", headers=headers, follow_redirects=False).status_code == 403
    with open_session(world) as s:
        assert s.scalars(select(AccountException)).all() == []
        assert s.scalars(select(AuditLog)).all() == []
    assert _identity(world, "Person 1").account_type_override is None


def test_start_run_runs_the_cycle_in_the_background_and_is_audited(
    world: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []

    def fake_cycle(_factory: Any, _settings: Any, slug: str) -> None:
        started.append(slug)

    monkeypatch.setattr("app.web.runs.run_tenant_cycle", fake_cycle)
    r = world.post("/runs/cg/start", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/runs?done=started")
    assert started == ["cg"]  # Hintergrundaufgabe lief nach der Antwort
    assert "Abruf gestartet" in world.get("/runs?done=started").text
    with open_session(world) as s:
        (entry,) = s.scalars(select(AuditLog)).all()
        assert (entry.action, entry.actor) == ("sync.manual", "lokal (ohne Anmeldung)")


def test_start_run_is_refused_while_a_cycle_runs_for_the_tenant(
    world: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import cycle

    monkeypatch.setattr("app.web.runs.run_tenant_cycle", lambda *_a: pytest.fail("läuft doppelt"))
    lock = cycle._lock("cg")
    assert lock.acquire(blocking=False)
    try:
        r = world.post("/runs/cg/start", follow_redirects=False)
    finally:
        lock.release()
    assert r.headers["location"] == "/runs?done=busy"
    with open_session(world) as s:
        assert s.scalars(select(AuditLog)).all() == []


def test_start_run_for_unknown_or_disabled_tenant(world: TestClient) -> None:
    assert world.post("/runs/gibtsnicht/start", follow_redirects=False).status_code == 404
    with open_session(world) as s, s.begin():
        get_tenant(s, "cvh").enabled = False
    r = world.post("/runs/cvh/start", follow_redirects=False)
    assert r.headers["location"] == "/runs?done=unknown"
