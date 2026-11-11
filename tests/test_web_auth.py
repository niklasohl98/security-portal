from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import service
from app.auth.context import COOKIE_NAME
from app.config import Settings
from app.db.models import AppSession, AppUser, AuditLog
from app.main import create_app
from tests.conftest import TEST_PASSWORD, TEST_USER
from tests.identity_fakes import NOW
from tests.web_fakes import open_session

NEW_PASSWORD = "Ein-ganz-neues-Passwort-5"
GENERIC = "Anmeldung nicht möglich"
PROTECTED_GET = [
    "/",
    "/accounts",
    "/accounts/cg/1",
    "/exceptions",
    "/runs",
    "/review",
    "/export?format=csv",
    "/password",
]


def _login(client: TestClient, username: str, password: str, **extra: str) -> httpx.Response:
    fields = {"username": username, "password": password, **extra}
    response: httpx.Response = client.post("/login", data=fields, follow_redirects=False)
    return response


def _token(client: TestClient) -> str:
    return client.cookies.get(COOKIE_NAME) or ""


def _csrf(client: TestClient, token: str | None = None) -> str:
    with open_session(client) as s:
        info = service.load_session(s, token or _token(client), NOW)
        assert info is not None
        return info.csrf_token


def _login_initial(client: TestClient) -> None:
    assert _login(client, "Admin", "Passwort123").status_code == 303


# --- Zugriffsschutz ---


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_pages_redirect_to_the_login(anon: TestClient, path: str) -> None:
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


def test_the_original_target_is_kept_as_next(anon: TestClient) -> None:
    r = anon.get("/accounts?status=inactive&q=a b", follow_redirects=False)
    nxt = parse_qs(urlsplit(r.headers["location"]).query)["next"]
    assert nxt == ["/accounts?status=inactive&q=a%20b"]


@pytest.mark.parametrize(
    "path",
    [
        "/accounts/cg/1/exception",
        "/exceptions/cg/1/delete",
        "/runs/cg/start",
        "/review/cg/1",
        "/logout",
        "/password",
    ],
)
def test_unauthenticated_posts_get_401_and_change_nothing(anon: TestClient, path: str) -> None:
    r = anon.post(path, data={"decision": "delete", "csrf_token": "x"}, follow_redirects=False)
    assert r.status_code == 401
    with open_session(anon) as s:
        assert [a.action for a in s.scalars(select(AuditLog))] == [
            "user.create"
        ]  # nur das Initialkonto


def test_public_paths_need_no_session(anon: TestClient) -> None:
    assert anon.get("/health").status_code == 200
    assert anon.get("/login").status_code == 200
    assert anon.get("/static/style.css").status_code == 200


def test_a_tampered_or_foreign_cookie_is_anonymous(anon: TestClient) -> None:
    for value in ("x", "a" * 400, "../etc/passwd", ""):
        anon.cookies.set(COOKIE_NAME, value)
        assert anon.get("/", follow_redirects=False).status_code == 303


def test_redirects_and_login_page_carry_the_security_headers(anon: TestClient) -> None:
    for r in (anon.get("/", follow_redirects=False), anon.get("/login")):
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
    assert anon.get("/login").headers["cache-control"] == "no-store"


# --- Anmeldung ---


def test_login_page_has_a_password_field_and_no_nav(anon: TestClient) -> None:
    text = anon.get("/login").text
    assert 'type="password"' in text
    assert 'autocomplete="current-password"' in text
    assert 'href="/accounts"' not in text  # keine Navigation ohne Anmeldung
    assert "Abmelden" not in text


def test_login_sets_a_hardened_cookie_and_stores_only_the_hash(anon: TestClient) -> None:
    r = _login(anon, TEST_USER, "x")  # Benutzer gibt es noch nicht
    assert r.status_code == 401
    _login_initial(anon)
    cookie = anon.cookies.get(COOKIE_NAME)
    assert cookie
    after = anon.get("/login", follow_redirects=False)
    assert after.headers.get_list("set-cookie") == []  # nur beim Login wird gesetzt
    with open_session(anon) as s:
        rows = s.scalars(select(AppSession)).all()
        assert len(rows) == 1
        assert cookie not in (rows[0].token_hash, rows[0].csrf_token)


def test_cookie_flags(settings: Settings, frozen_clock: object) -> None:
    with TestClient(create_app(settings)) as client:
        r = _login(client, "Admin", "Passwort123")
    header = r.headers["set-cookie"]
    assert header.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert f"Max-Age={settings.session_hours * 3600}" in header
    assert "Secure" not in header  # über http kein Secure (sonst käme das Cookie nie zurück)


def test_cookie_is_secure_over_https_or_when_forced(
    settings: Settings, frozen_clock: object
) -> None:
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert "Secure" in _login(client, "Admin", "Passwort123").headers["set-cookie"]
    forced = replace(settings, cookie_secure=True)
    with TestClient(create_app(forced)) as client:
        assert "Secure" in _login(client, "Admin", "Passwort123").headers["set-cookie"]
    off = replace(settings, cookie_secure=False)
    with TestClient(create_app(off), base_url="https://testserver") as client:
        assert "Secure" not in _login(client, "Admin", "Passwort123").headers["set-cookie"]


def test_every_login_failure_looks_the_same(anon: TestClient) -> None:
    unknown = _login(anon, "gibtsnicht", "Falsches-Passwort-1")
    wrong = _login(anon, "Admin", "Falsches-Passwort-1")
    assert unknown.status_code == wrong.status_code == 401
    assert (
        unknown.text.replace("gibtsnicht", "Admin") == wrong.text
    )  # bis auf den Namen im Formular
    assert GENERIC in unknown.text
    assert COOKIE_NAME not in unknown.headers.get("set-cookie", "")
    assert COOKIE_NAME not in wrong.headers.get("set-cookie", "")


def test_the_account_locks_after_repeated_failures_without_saying_so(
    anon: TestClient, settings: Settings
) -> None:
    for _ in range(settings.login_max_attempts):
        assert _login(anon, "Admin", "Falsches-Passwort-1").status_code == 401
    locked = _login(anon, "Admin", "Passwort123")  # das richtige Passwort hilft jetzt nicht
    assert locked.status_code == 401
    assert GENERIC in locked.text
    assert "gesperrt" in locked.text  # nur im allgemeinen Satz, nie als eigene Auskunft
    assert COOKIE_NAME not in locked.headers.get("set-cookie", "")


def test_login_input_is_escaped_in_the_form(anon: TestClient) -> None:
    r = _login(anon, '"><script>alert(1)</script>', "x")
    assert r.status_code == 401
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_cross_site_login_posts_are_refused(anon: TestClient) -> None:
    r = anon.post(
        "/login",
        data={"username": "Admin", "password": "Passwort123"},
        headers={"origin": "https://boese.example"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert COOKIE_NAME not in r.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example/x",
        "https://evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "evil",
        "/login",
        "/logout",
        "/a\r\nb",
    ],
)
def test_login_never_redirects_to_other_sites(anon: TestClient, target: str) -> None:
    _login_initial(anon)
    with open_session(anon) as s, s.begin():  # Zwang abschalten, damit das Ziel zählt
        s.scalars(select(AppUser)).one().must_change_password = False
    anon.cookies.clear()
    r = _login(anon, "Admin", "Passwort123", next=target)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_login_redirects_to_a_safe_local_target(anon: TestClient) -> None:
    _login_initial(anon)
    with open_session(anon) as s, s.begin():
        s.scalars(select(AppUser)).one().must_change_password = False
    anon.cookies.clear()
    r = _login(anon, "Admin", "Passwort123", next="/accounts?status=never")
    assert r.headers["location"] == "/accounts?status=never"


def test_logged_in_users_skip_the_login_page(site: TestClient) -> None:
    r = site.get("/login", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/")


def test_every_login_issues_a_new_token(anon: TestClient) -> None:
    _login_initial(anon)
    first = _token(anon)
    _login_initial(anon)
    assert _token(anon) != first


# --- Zwang zum Passwortwechsel ---


def test_the_initial_account_can_only_reach_the_password_page(anon: TestClient) -> None:
    r = _login(anon, "Admin", "Passwort123")
    assert r.headers["location"] == "/password"
    for path in PROTECTED_GET:
        if path == "/password":
            continue
        got = anon.get(path, follow_redirects=False)
        assert (got.status_code, got.headers["location"]) == (303, "/password"), path
    assert anon.get("/password").status_code == 200
    assert anon.get("/static/style.css").status_code == 200
    assert anon.get("/health").status_code == 200


def test_the_forced_user_cannot_post_anything_but_password_and_logout(anon: TestClient) -> None:
    _login_initial(anon)
    token = _csrf(anon)
    for path in (
        "/accounts/cg/1/exception",
        "/exceptions/cg/1/delete",
        "/runs/cg/start",
        "/review/cg/1",
    ):
        r = anon.post(
            path, data={"csrf_token": token, "decision": "delete"}, follow_redirects=False
        )
        assert r.status_code == 403, path


def test_the_forced_page_explains_and_hides_the_navigation(anon: TestClient) -> None:
    _login_initial(anon)
    text = anon.get("/password").text
    assert "müssen das Passwort ändern" in text
    assert 'href="/accounts"' not in text
    assert "Abmelden" in text
    assert "Admin" in text


@pytest.mark.parametrize(
    ("new", "confirm", "message"),
    [
        ("Passwort123", "Passwort123", "Standardpasswort"),
        ("zu-kurz", "zu-kurz", "mindestens 12 Zeichen"),
        ("Ein-ganz-neues-Passwort-5", "Anders-Anders-Passwort-6", "stimmen nicht überein"),
        ("admin-ist-hier-12345", "admin-ist-hier-12345", "Benutzernamen"),
    ],
)
def test_weak_new_passwords_are_refused_with_a_reason(
    anon: TestClient, new: str, confirm: str, message: str
) -> None:
    _login_initial(anon)
    r = anon.post(
        "/password",
        data={
            "csrf_token": _csrf(anon),
            "current_password": "Passwort123",
            "new_password": new,
            "confirm_password": confirm,
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert message in r.text
    assert anon.get("/", follow_redirects=False).headers["location"] == "/password"  # Zwang bleibt


def test_the_wrong_current_password_is_refused_and_counts(
    anon: TestClient, settings: Settings
) -> None:
    _login_initial(anon)
    for _ in range(settings.login_max_attempts):
        r = anon.post(
            "/password",
            data={
                "csrf_token": _csrf(anon),
                "current_password": "falsch-falsch-1",
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        assert "aktuelle Passwort ist nicht korrekt" in r.text
    r = anon.post(
        "/password",
        data={
            "csrf_token": _csrf(anon),
            "current_password": "Passwort123",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert "Zu viele Fehlversuche" in r.text  # gesperrt, auch mit dem richtigen Passwort


def test_the_password_change_unlocks_everything_and_rotates_the_session(anon: TestClient) -> None:
    _login_initial(anon)
    old_token = _token(anon)
    r = anon.post(
        "/password",
        data={
            "csrf_token": _csrf(anon),
            "current_password": "Passwort123",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert (r.status_code, r.headers["location"]) == (303, "/")
    assert _token(anon) != old_token  # neue Sitzung nach dem Wechsel
    assert anon.get("/").status_code == 200  # Zwang aufgehoben
    assert "Abmelden" in anon.get("/").text
    assert "Admin" in anon.get("/").text
    with open_session(anon) as s:
        assert service.load_session(s, old_token, NOW) is None  # alte Sitzung ist beendet
        assert "auth.password_change" in [a.action for a in s.scalars(select(AuditLog))]
    anon.cookies.clear()
    assert _login(anon, "Admin", "Passwort123").status_code == 401  # das Startpasswort ist tot
    ok = _login(anon, "Admin", NEW_PASSWORD)
    assert (ok.status_code, ok.headers["location"]) == (303, "/")


def test_changing_the_password_ends_all_other_sessions(
    site: TestClient, settings: Settings
) -> None:
    with open_session(site) as s, s.begin():
        uid = s.scalars(select(AppUser).where(AppUser.username == TEST_USER)).one().id
        other = service.create_session(s, settings, uid, NOW)
    r = site.post(
        "/password",
        data={
            "csrf_token": _csrf(site),
            "current_password": TEST_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with open_session(site) as s:
        assert service.load_session(s, other.token, NOW) is None


# --- Abmelden, CSRF, Ablauf ---


def test_logout_ends_the_session_clears_the_cookie_and_is_audited(site: TestClient) -> None:
    token = _token(site)
    r = site.post("/logout", data={"csrf_token": site.csrf_token}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    assert f"{COOKIE_NAME}=" in r.headers["set-cookie"]
    assert "Max-Age=0" in r.headers["set-cookie"] or "expires" in r.headers["set-cookie"].lower()
    with open_session(site) as s:
        assert service.load_session(s, token, NOW) is None  # auch das alte Token ist tot
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "auth.logout")).one()
        assert entry.actor == TEST_USER
    site.cookies.set(COOKIE_NAME, token)  # das gestohlene Token funktioniert nicht mehr
    assert site.get("/", follow_redirects=False).status_code == 303


def test_logout_is_not_a_get(site: TestClient) -> None:
    assert site.get("/logout").status_code == 405


@pytest.mark.parametrize("token", [None, "", "falsch", "a" * 43])
def test_posts_need_the_csrf_token_of_the_session(site: TestClient, token: str | None) -> None:
    body = "" if token is None else f"csrf_token={token}"
    r = site.post(
        "/logout",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert site.get("/").status_code == 200  # weiterhin angemeldet


def test_the_token_of_another_session_is_not_accepted(site: TestClient, settings: Settings) -> None:
    with open_session(site) as s, s.begin():
        uid = s.scalars(select(AppUser).where(AppUser.username == TEST_USER)).one().id
        other = service.create_session(s, settings, uid, NOW)
    r = site.post("/logout", data={"csrf_token": other.csrf_token}, follow_redirects=False)
    assert r.status_code == 403


def test_every_form_in_the_pages_carries_the_token(site: TestClient) -> None:
    for path in ("/", "/exceptions", "/runs", "/review", "/password"):
        text = site.get(path).text
        assert f'name="csrf_token" value="{site.csrf_token}"' in text


def test_sessions_expire(
    site: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert site.get("/").status_code == 200
    later = NOW + timedelta(hours=settings.session_hours, seconds=1)
    monkeypatch.setattr("app.auth.middleware.utcnow", lambda: later)
    r = site.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"].split("?")[0]) == (303, "/login")


def test_deactivated_users_lose_access_immediately(site: TestClient) -> None:
    with open_session(site) as s, s.begin():
        s.scalars(select(AppUser).where(AppUser.username == TEST_USER)).one().is_active = False
    assert site.get("/", follow_redirects=False).status_code == 303


def test_navigation_shows_the_user_and_logout(site: TestClient) -> None:
    text = site.get("/").text
    assert "Abmelden" in text
    assert TEST_USER in text
    assert "logout-button" in text
    assert 'href="/password"' in text
