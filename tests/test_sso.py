from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import service, users
from app.auth.context import COOKIE_NAME
from app.auth.sso_provider import EntraGrant, EntraProvider, SsoError
from app.auth.sso_runtime import FLOW_SECONDS
from app.auth.sso_settings import SsoSettings
from app.config import ConfigError, Settings
from app.db.models import AppSession, AppUser
from app.main import create_app
from tests.identity_fakes import NOW
from tests.web_fakes import open_session

TENANT = "00000000-0000-4000-8000-000000000001"
CLIENT = "00000000-0000-4000-8000-000000000002"
GROUP = "00000000-0000-4000-8000-000000000003"
ADMIN = "00000000-0000-4000-8000-000000000004"
OID = "00000000-0000-4000-8000-000000000005"
PUBLIC = "https://accounts.example"
CFG = SsoSettings(TENANT, CLIENT, GROUP, "sso_cert", "A" * 40, ADMIN)


class FakeProvider(EntraProvider):
    """Protokollgrenze ersetzen; Browserbindung, Datenbank und Zulassungslogik bleiben echt."""

    def __init__(self, cfg: SsoSettings, secrets_dir: Path, public_url: str) -> None:
        self.cfg = cfg
        self.memberships = {GROUP, ADMIN}
        self.closed = False
        self.group_calls = 0
        self.unavailable = False

    def close(self) -> None:
        self.closed = True

    def begin(self) -> dict[str, Any]:
        return {
            "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
            "state": "state",
            "nonce": "nonce",
        }

    def complete(self, flow: dict[str, Any], response: dict[str, str]) -> EntraGrant:
        if response != {"state": flow["state"], "code": "valid"}:
            raise SsoError("auth")
        return EntraGrant(OID, None)

    def groups(self, grant: EntraGrant) -> set[str]:
        self.group_calls += 1
        if self.unavailable:
            raise SsoError("groups")
        return self.memberships.copy()


@pytest.fixture
def sso_site(
    settings: Settings,
    frozen_clock: object,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, list[FakeProvider]]]:
    providers: list[FakeProvider] = []

    def provider(cfg: SsoSettings, secrets_dir: Path, public_url: str) -> FakeProvider:
        instance = FakeProvider(cfg, secrets_dir, public_url)
        providers.append(instance)
        return instance

    monkeypatch.setattr("app.auth.sso_runtime.EntraProvider", provider)
    cfg = replace(settings, public_url=PUBLIC, sso=CFG)
    with TestClient(create_app(cfg), base_url=PUBLIC) as client:
        yield client, providers


def _start(client: TestClient) -> None:
    response = client.get("/auth/entra/start?next=/accounts", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("https://login.microsoftonline.com/")
    assert "SameSite=none" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]


def _complete(client: TestClient) -> None:
    response = client.post(
        "/auth/entra/callback", data={"state": "state", "code": "valid"}, follow_redirects=False
    )
    assert response.headers["location"] == "/accounts"


def test_sso_default_is_disabled_and_public_endpoints_do_not_enable_it(anon: TestClient) -> None:
    assert SsoSettings.from_env({}) is None
    assert "Mit Microsoft Entra" not in anon.get("/login").text
    assert anon.get("/auth/entra/start").status_code == 404
    assert anon.post("/auth/entra/callback", data={"code": "unused"}).status_code == 404


def test_sso_environment_is_explicit_and_requires_https() -> None:
    env = {
        "KW_SSO_ENABLED": "1",
        "KW_SSO_TENANT_ID": TENANT,
        "KW_SSO_CLIENT_ID": CLIENT,
        "KW_SSO_ALLOWED_GROUP_ID": GROUP,
        "KW_SSO_ADMIN_GROUP_ID": ADMIN,
        "KW_SSO_CERT_SECRET": "sso_cert",
        "KW_SSO_CERT_THUMBPRINT": "a" * 40,
    }
    assert SsoSettings.from_env(env) == CFG
    assert SsoSettings.from_env({**env, "KW_SSO_ADMIN_GROUP_ID": ""}) == replace(
        CFG, admin_group_id=None
    )
    with pytest.raises(ConfigError, match="KW_PUBLIC_URL"):
        Settings.from_env(env)
    assert Settings.from_env({**env, "KW_PUBLIC_URL": PUBLIC}).sso == CFG
    for key, value in (
        ("KW_SSO_ENABLED", "maybe"),
        ("KW_SSO_TENANT_ID", "bad"),
        ("KW_SSO_CERT_SECRET", "../bad"),
        ("KW_SSO_CERT_THUMBPRINT", "bad"),
        ("KW_SSO_RECHECK_SECONDS", "901"),
        ("KW_SSO_RECHECK_SECONDS", "0"),
    ):
        with pytest.raises(ConfigError, match=key):
            SsoSettings.from_env({**env, key: value})


def test_successful_sso_creates_only_external_identity_and_hardened_session(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, providers = sso_site
    assert "Mit Microsoft Entra" in client.get("/login").text
    _start(client)
    _complete(client)
    assert client.get("/accounts").status_code == 200
    assert "Passwort ändern" not in client.get("/").text
    assert client.get("/password").status_code == 403
    assert client.get("/users").status_code == 200
    with open_session(client) as session:
        user = session.scalars(select(AppUser).where(AppUser.oidc_object_id == OID)).one()
        assert user.oidc_tenant_id == TENANT
        assert user.password_hash == "!"
        row = session.scalars(select(AppSession).where(AppSession.user_id == user.id)).one()
        assert row.is_oidc
        app_cfg: Settings = client.app.state.settings  # type: ignore[attr-defined]
        assert service.authenticate(session, app_cfg, user.username, "anything").user_id is None
        with pytest.raises(service.UserError, match="external"):
            service.reset_password(session, user.username, "Ein-lang-genug-Passwort-8", "test")
        assert providers[0].group_calls == 1  # Cache innerhalb der Prüfzeit


@pytest.mark.parametrize("mode", ["missing_cookie", "wrong_state", "replay", "expired"])
def test_callback_rejects_unbound_replayed_and_expired_flows(
    sso_site: tuple[TestClient, list[FakeProvider]],
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = sso_site
    _start(client)
    if mode == "missing_cookie":
        client.cookies.clear()
    if mode == "replay":
        _complete(client)
    if mode == "expired":
        monkeypatch.setattr(
            "app.auth.sso_runtime.utcnow", lambda: NOW + timedelta(seconds=FLOW_SECONDS)
        )
    response = client.post(
        "/auth/entra/callback",
        data={"state": "wrong" if mode == "wrong_state" else "state", "code": "valid"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login?sso_error=1"
    assert response.status_code == 303


@pytest.mark.parametrize("failure", ["group", "network", "admin", "restart"])
def test_revalidation_enforces_group_and_admin_revocation_and_restart(
    sso_site: tuple[TestClient, list[FakeProvider]],
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, providers = sso_site
    _start(client)
    _complete(client)
    if failure == "group":
        providers[0].memberships.clear()
    elif failure == "network":
        providers[0].unavailable = True
    elif failure == "admin":
        providers[0].memberships.remove(ADMIN)
    else:
        client.app.state.sso.close()  # type: ignore[attr-defined]
    monkeypatch.setattr("app.auth.sso_runtime.utcnow", lambda: NOW + timedelta(seconds=300))
    response = client.get("/users", follow_redirects=False)
    assert response.status_code == (403 if failure == "admin" else 303)
    if failure == "admin":
        assert client.get("/accounts").status_code == 200
    else:
        with open_session(client) as session:
            assert not list(session.scalars(select(AppSession)))


def test_group_missing_at_login_never_creates_session(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, providers = sso_site
    _start(client)
    providers[0].memberships.clear()
    assert (
        client.post(
            "/auth/entra/callback", data={"state": "state", "code": "valid"}, follow_redirects=False
        ).headers["location"]
        == "/login?sso_error=1"
    )
    assert COOKIE_NAME not in client.cookies


def test_sso_logout_clears_cache_and_local_emergency_admin_survives(
    sso_site: tuple[TestClient, list[FakeProvider]],
) -> None:
    client, providers = sso_site
    _start(client)
    _complete(client)
    token = client.cookies[COOKIE_NAME]
    with open_session(client) as session:
        info = service.load_session(session, token, NOW)
        assert info is not None
        local = session.scalars(select(AppUser).where(AppUser.username == "Admin")).one()
        with pytest.raises(users.UserAdminError, match="last_admin"):
            users.set_admin(session, local.id, False, info.user_id, "test", NOW)
        with pytest.raises(users.UserAdminError, match="external"):
            users.reset_with_temp_password(session, info.user_id, local.id, "test")
        with pytest.raises(users.UserAdminError, match="external"):
            users.set_admin(session, info.user_id, False, local.id, "test")
    assert (
        client.post(
            "/logout", data={"csrf_token": info.csrf_token}, follow_redirects=False
        ).status_code
        == 303
    )
    assert providers[0].closed
    assert (
        client.post(
            "/login", data={"username": "Admin", "password": "Passwort123"}, follow_redirects=False
        ).headers["location"]
        == "/password"
    )


def test_flow_memory_is_bounded_and_next_cannot_redirect_off_site(
    sso_site: tuple[TestClient, list[FakeProvider]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = sso_site
    monkeypatch.setattr("app.auth.sso_runtime.MAX_FLOWS", 1)
    client.get("/auth/entra/start?next=https://foreign.example", follow_redirects=False)
    assert (
        client.get("/auth/entra/start", follow_redirects=False).headers["location"]
        == "/login?sso_error=1"
    )


def test_session_memory_limit_fails_closed(
    sso_site: tuple[TestClient, list[FakeProvider]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = sso_site
    _start(client)
    monkeypatch.setattr("app.auth.sso_runtime.MAX_SESSIONS", 0)
    response = client.post(
        "/auth/entra/callback", data={"state": "state", "code": "valid"}, follow_redirects=False
    )
    assert response.headers["location"] == "/login?sso_error=1"
    with open_session(client) as session:
        assert not list(session.scalars(select(AppSession)))
