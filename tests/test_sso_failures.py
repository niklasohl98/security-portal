from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import service
from app.auth.sso_provider import SsoError
from app.auth.sso_runtime import SsoRuntime
from app.config import Settings
from app.db.models import AppUser
from app.main import create_app
from tests.identity_fakes import NOW
from tests.test_sso import OID, PUBLIC, FakeProvider, _complete, _start
from tests.test_sso import sso_site as sso_site
from tests.web_fakes import open_session


def test_repeated_login_reuses_identity_and_expired_cache_is_closed(
    sso_site: tuple[TestClient, list[FakeProvider]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, providers = sso_site
    _start(client)
    _complete(client)
    client.cookies.clear()
    later = NOW + timedelta(hours=9)
    for module in (
        "app.auth.sso_runtime",
        "app.auth.sso_users",
        "app.auth.service",
        "app.auth.middleware",
    ):
        monkeypatch.setattr(module + ".utcnow", lambda: later)
    _start(client)
    assert providers[0].closed
    _complete(client)
    with open_session(client) as session:
        assert len(list(session.scalars(select(AppUser).where(AppUser.oidc_object_id == OID)))) == 1


def test_disabled_external_user_and_local_name_collision_never_link_accounts(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, _ = sso_site
    with open_session(client) as session, session.begin():
        uid = service.create_user(session, f"Entra-{OID}", "Ein-sicheres-Passwort-7", "test")
    _start(client)
    response = client.post(
        "/auth/entra/callback", data={"state": "state", "code": "valid"}, follow_redirects=False
    )
    assert response.headers["location"] == "/login?sso_error=1"
    with open_session(client) as session, session.begin():
        local = session.get_one(AppUser, uid)
        assert local.oidc_object_id is None
        local.username = "OtherLocal"
        local.username_key = "otherlocal"
    _start(client)
    _complete(client)
    client.cookies.clear()
    with open_session(client) as session, session.begin():
        user = session.scalars(select(AppUser).where(AppUser.oidc_object_id == OID)).one()
        user.is_active = False
    _start(client)
    assert (
        client.post(
            "/auth/entra/callback", data={"state": "state", "code": "valid"}, follow_redirects=False
        ).headers["location"]
        == "/login?sso_error=1"
    )


def test_disabling_sso_rejects_old_sessions_and_retains_local_login(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, _ = sso_site
    _start(client)
    _complete(client)
    cfg: Settings = client.app.state.settings  # type: ignore[attr-defined]
    with TestClient(create_app(replace(cfg, sso=None)), base_url=PUBLIC) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/accounts", follow_redirects=False).status_code == 303
        assert (
            restarted.post(
                "/login",
                data={"username": "Admin", "password": "Passwort123"},
                follow_redirects=False,
            ).headers["location"]
            == "/password"
        )


def test_invalid_session_cookie_is_pruned_from_sso_memory(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, _ = sso_site
    client.cookies.set("kw_session", "synthetic-invalid")
    assert client.get("/accounts", follow_redirects=False).status_code == 303


def test_external_users_cannot_change_their_password_through_a_direct_post(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, _ = sso_site
    _start(client)
    _complete(client)
    with open_session(client) as session:
        info = service.load_session(session, client.cookies["kw_session"], NOW)
        assert info is not None
        user = session.get_one(AppUser, info.user_id)
        original_hash = user.password_hash
    response = client.post(
        "/password",
        data={
            "csrf_token": info.csrf_token,
            "current_password": "synthetic",
            "new_password": "Ein-sicheres-neues-Passwort-7",
            "confirm_password": "Ein-sicheres-neues-Passwort-7",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403
    with open_session(client) as session:
        assert session.get_one(AppUser, info.user_id).password_hash == original_hash


@pytest.mark.parametrize("flow", [{"auth_uri": "https://foreign.example/"}, {}])
def test_untrusted_or_missing_authorization_endpoint_is_rejected(
    sso_site: tuple[TestClient, list[FakeProvider]],
    monkeypatch: pytest.MonkeyPatch,
    flow: dict[str, Any],
) -> None:
    client, providers = sso_site
    monkeypatch.setattr(FakeProvider, "begin", lambda _self: flow)
    assert (
        client.get("/auth/entra/start", follow_redirects=False).headers["location"]
        == "/login?sso_error=1"
    )
    assert providers[0].closed


def test_parallel_login_initialization_cannot_exceed_memory_limit(
    sso_site: tuple[TestClient, list[FakeProvider]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, providers = sso_site
    runtime: SsoRuntime = client.app.state.sso  # type: ignore[attr-defined]
    barrier = threading.Barrier(2)
    original = FakeProvider.begin

    def begin(provider: FakeProvider) -> dict[str, Any]:
        barrier.wait(timeout=5)
        return original(provider)

    monkeypatch.setattr(FakeProvider, "begin", begin)
    monkeypatch.setattr("app.auth.sso_runtime.MAX_FLOWS", 1)

    def login(_unused: int) -> bool:
        try:
            runtime.begin("/")
            return True
        except SsoError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(login, range(2))) == [False, True]
    assert sum(provider.closed for provider in providers) == 1
