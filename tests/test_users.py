from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import service, users
from app.auth.passwords import check_new_password
from app.auth.service import AuthOutcome, UserError
from app.auth.users import UserAdminError
from app.config import Settings
from app.db.models import AppUser, AuditLog
from tests.conftest import TEST_USER
from tests.identity_fakes import NOW
from tests.web_fakes import open_session, post_form

PASSWORD = "Ein-gutes-Passwort-1"


def _make(s: Session, name: str, *, admin: bool = False, active: bool = True) -> int:
    uid = service.create_user(s, name, PASSWORD, "test", NOW, is_admin=admin)
    s.get_one(AppUser, uid).is_active = active
    return uid


def _actions(s: Session) -> list[str]:
    return [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


# --- Service ---


def test_temp_passwords_are_random_unambiguous_and_policy_conform() -> None:
    seen = {users.generate_temp_password("anna") for _ in range(50)}
    assert len(seen) == 50
    for pw in seen:
        assert len(pw) == users.TEMP_PASSWORD_LENGTH
        assert not set(pw) & set("0O1lI")
        assert check_new_password(pw, pw, username="anna") == []


def test_a_username_inside_the_password_is_never_generated() -> None:
    assert all("ab" not in users.generate_temp_password("ab").casefold() for _ in range(200))


def test_create_with_temp_password_works_once_and_forces_a_change(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid, password = users.create_with_temp_password(s, " Berta ", False, "Admin", NOW)
    with factory.begin() as s:
        user = s.get_one(AppUser, uid)
        assert (user.username, user.is_admin, user.must_change_password) == ("Berta", False, True)
        assert password not in user.password_hash
        assert service.authenticate(s, settings, "Berta", password, NOW).outcome is AuthOutcome.OK
        audit = s.scalars(select(AuditLog)).all()
        assert [(a.action, a.actor, a.detail) for a in audit if a.action == "user.create"] == [
            ("user.create", "Admin", f"user_id={uid}")
        ]
        assert all(password not in (a.detail or "") for a in audit)  # nie im Audit-Log


def test_create_rejects_bad_and_duplicate_names(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        _make(s, "Berta")
    with factory() as s, pytest.raises(UserError) as err:
        users.create_with_temp_password(s, "berta", False, "Admin", NOW)
    assert err.value.code == "exists"
    with factory() as s, pytest.raises(UserError) as err:
        users.create_with_temp_password(s, "x y", False, "Admin", NOW)
    assert err.value.code == "bad_name"


def test_reset_gives_a_new_temp_password_ends_sessions_and_unlocks(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        admin = _make(s, "Chef", admin=True)
        other = _make(s, "Berta", active=False)
        sess = service.create_session(s, settings, other, NOW)
        s.get_one(AppUser, other).failed_attempts = 3
    with factory.begin() as s:
        name, password = users.reset_with_temp_password(s, other, admin, "Chef", NOW)
    assert name == "Berta"
    with factory.begin() as s:
        assert service.load_session(s, sess.token, NOW) is None
        user = s.get_one(AppUser, other)
        assert (user.is_active, user.must_change_password, user.failed_attempts) == (True, True, 0)
        assert service.authenticate(s, settings, "Berta", password, NOW).outcome is AuthOutcome.OK
        assert "user.reset_password" in _actions(s)


def test_nobody_manages_their_own_account_here(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        admin = _make(s, "Chef", admin=True)
        _make(s, "Zweiter", admin=True)
    for call in (
        lambda s: users.reset_with_temp_password(s, admin, admin, "Chef", NOW),
        lambda s: users.set_active(s, admin, False, admin, "Chef", NOW),
        lambda s: users.set_admin(s, admin, False, admin, "Chef", NOW),
    ):
        with factory() as s, pytest.raises(UserAdminError) as err:
            call(s)
        assert err.value.code == "self"


def test_deactivating_ends_sessions_and_activating_restores_access(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        admin = _make(s, "Chef", admin=True)
        other = _make(s, "Berta")
        sess = service.create_session(s, settings, other, NOW)
    with factory.begin() as s:
        users.set_active(s, other, False, admin, "Chef", NOW)
    with factory.begin() as s:
        assert service.load_session(s, sess.token, NOW) is None
        assert (
            service.authenticate(s, settings, "Berta", PASSWORD, NOW).outcome is AuthOutcome.INVALID
        )
        users.set_active(s, other, True, admin, "Chef", NOW)
        users.set_active(s, other, True, admin, "Chef", NOW)  # zweimal: keine Doppelbuchung
    with factory() as s:
        assert service.authenticate(s, settings, "Berta", PASSWORD, NOW).outcome is AuthOutcome.OK
        assert _actions(s).count("user.activate") == 1
        assert "user.deactivate" in _actions(s)


def test_the_last_active_admin_cannot_be_removed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        actor = _make(
            s, "Nur-Benutzer"
        )  # handelt, ist aber kein Admin (z. B. über die Befehlszeile)
        only = _make(s, "Einziger", admin=True)
        _make(s, "Inaktiver-Admin", admin=True, active=False)  # zählt nicht
    with factory() as s, pytest.raises(UserAdminError) as err:
        users.set_active(s, only, False, actor, "x", NOW)
    assert err.value.code == "last_admin"
    with factory() as s, pytest.raises(UserAdminError) as err:
        users.set_admin(s, only, False, actor, "x", NOW)
    assert err.value.code == "last_admin"
    with factory.begin() as s:
        _make(s, "Zweiter", admin=True)
    with factory.begin() as s:
        users.set_admin(s, only, False, actor, "x", NOW)  # jetzt gibt es einen weiteren


def test_admin_role_changes_are_audited_once(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        admin = _make(s, "Chef", admin=True)
        other = _make(s, "Berta")
    with factory.begin() as s:
        users.set_admin(s, other, True, admin, "Chef", NOW)
        users.set_admin(s, other, True, admin, "Chef", NOW)
        users.set_admin(s, other, False, admin, "Chef", NOW)
    with factory() as s:
        assert [a for a in _actions(s) if a.startswith("user.admin")] == [
            "user.admin_on",
            "user.admin_off",
        ]


def test_unknown_users_are_reported(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        admin = _make(s, "Chef", admin=True)
    for call in (
        lambda s: users.reset_with_temp_password(s, 999, admin, "Chef", NOW),
        lambda s: users.set_active(s, 999, False, admin, "Chef", NOW),
        lambda s: users.set_admin(s, 999, True, admin, "Chef", NOW),
    ):
        with factory() as s, pytest.raises(UserAdminError) as err:
            call(s)
        assert err.value.code == "not_found"


def test_the_list_shows_state_without_secrets(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        _make(s, "Berta")
        locked = _make(s, "Gesperrt")
        s.get_one(AppUser, locked).locked_until = NOW + timedelta(minutes=5)
        s.get_one(AppUser, locked).must_change_password = False
    with factory() as s:
        rows = {r.username: r for r in users.list_user_rows(s, NOW)}
    assert rows["Gesperrt"].locked is True
    assert rows["Berta"].locked is False
    assert not hasattr(rows["Berta"], "password_hash")


def test_the_initial_admin_and_existing_users_are_admins(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        service.ensure_initial_admin(s, NOW)
    with factory() as s:
        assert s.scalars(select(AppUser)).one().is_admin is True


# --- Oberfläche ---


def _secret(html: str) -> str:
    match = re.search(r'<code class="secret">([^<]+)</code>', html)
    assert match is not None
    return match.group(1)


def _user_id(client: TestClient, name: str) -> int:
    with open_session(client) as s:
        return s.scalars(select(AppUser).where(AppUser.username == name)).one().id


def test_admins_see_the_user_page_and_the_navigation_link(site: TestClient) -> None:
    page = site.get("/users")
    assert page.status_code == 200
    assert TEST_USER in page.text
    assert 'href="/users"' in site.get("/").text


def test_members_get_no_page_no_link_and_no_actions(site: TestClient, settings: Settings) -> None:
    with open_session(site) as s, s.begin():
        member = _make(s, "Mitglied")
        s.get_one(AppUser, member).must_change_password = False
        new = service.create_session(s, settings, member, NOW)
        victim = _make(s, "Opfer")
    site.cookies.set("kw_session", new.token)
    token = new.csrf_token
    assert site.get("/users").status_code == 403
    assert 'href="/users"' not in site.get("/").text
    for path, fields in (
        ("/users", {"username": "Neu", "csrf_token": token}),
        (f"/users/{victim}/reset", {"csrf_token": token}),
        (f"/users/{victim}/active", {"csrf_token": token, "value": "0"}),
        (f"/users/{victim}/admin", {"csrf_token": token, "value": "1"}),
    ):
        body = "&".join(f"{k}={v}" for k, v in fields.items())
        r = site.post(
            path,
            content=body,
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert r.status_code == 403, path
    with open_session(site) as s:
        assert s.get_one(AppUser, victim).is_active is True
        assert [u.username for u in s.scalars(select(AppUser))].count("Neu") == 0


def test_creating_a_user_shows_the_password_once_and_it_works(site: TestClient) -> None:
    r = post_form(site, "/users", username="Berta")
    assert r.status_code == 200
    assert "nur jetzt" in r.text
    password = _secret(r.text)
    assert len(password) == users.TEMP_PASSWORD_LENGTH
    assert "Berta" in r.text
    assert password not in site.get("/users").text  # nicht noch einmal
    with open_session(site) as s:
        assert password not in " ".join(a.detail or "" for a in s.scalars(select(AuditLog)))
    site.cookies.clear()
    login = site.post(
        "/login", data={"username": "Berta", "password": password}, follow_redirects=False
    )
    assert (login.status_code, login.headers["location"]) == (303, "/password")  # Wechsel erzwungen


def test_creating_an_admin_and_error_cases(site: TestClient) -> None:
    r = post_form(site, "/users", username="Carla", is_admin="1")
    assert r.status_code == 200
    assert _admin(site, "Carla") is True
    dup = post_form(site, "/users", username="carla")
    assert dup.headers["location"] == "/users?err=exists"
    bad = post_form(site, "/users", username="a b")
    assert bad.headers["location"] == "/users?err=bad_name"
    assert "Diesen Benutzernamen gibt es schon" in site.get("/users?err=exists").text


def _admin(client: TestClient, name: str) -> bool:
    with open_session(client) as s:
        return s.scalars(select(AppUser).where(AppUser.username == name)).one().is_admin


def test_reset_shows_a_new_password_and_the_old_one_dies(
    site: TestClient, settings: Settings
) -> None:
    with open_session(site) as s, s.begin():
        _make(s, "Berta")
    r = post_form(site, f"/users/{_user_id(site, 'Berta')}/reset")
    assert r.status_code == 200
    assert "Passwort zurückgesetzt" in r.text
    new = _secret(r.text)
    with open_session(site) as s, s.begin():
        assert (
            service.authenticate(s, settings, "Berta", PASSWORD, NOW).outcome is AuthOutcome.INVALID
        )
        assert service.authenticate(s, settings, "Berta", new, NOW).outcome is AuthOutcome.OK


def test_deactivate_activate_and_role_buttons(site: TestClient) -> None:
    with open_session(site) as s, s.begin():
        _make(s, "Berta")
    uid = _user_id(site, "Berta")
    off = post_form(site, f"/users/{uid}/active", value="0")
    assert off.headers["location"] == "/users?done=inactive"
    assert "deaktiviert" in site.get("/users").text
    assert "Benutzer deaktiviert" in site.get("/users?done=inactive").text
    on = post_form(site, f"/users/{uid}/active", value="1")
    assert on.headers["location"] == "/users?done=active"
    promote = post_form(site, f"/users/{uid}/admin", value="1")
    assert promote.headers["location"] == "/users?done=admin_on"
    assert _admin(site, "Berta") is True
    assert (
        post_form(site, f"/users/{uid}/admin", value="0").headers["location"]
        == "/users?done=admin_off"
    )


def test_the_page_protects_the_own_account(site: TestClient) -> None:
    me = _user_id(site, TEST_USER)
    for path, value in (
        (f"/users/{me}/reset", None),
        (f"/users/{me}/active", "0"),
        (f"/users/{me}/admin", "0"),
    ):
        r = post_form(site, path) if value is None else post_form(site, path, value=value)
        assert r.headers["location"] == "/users?err=self"
    assert "lässt sich hier nicht ändern" in site.get("/users?err=self").text
    page = site.get("/users").text
    assert f'action="/users/{me}/reset"' not in page  # in der eigenen Zeile gibt es keine Aktionen
    assert "Sie" in page


def test_the_last_admin_message_is_shown(site: TestClient) -> None:
    # Über die Seite ist der Schutz nicht auslösbar (der Handelnde ist selbst aktiver Admin);
    # die feste Meldung muss trotzdem vorhanden sein.
    assert "mindestens ein aktiver Admin" in site.get("/users?err=last_admin").text


def test_unknown_user_ids_and_missing_csrf(site: TestClient) -> None:
    assert post_form(site, "/users/999/reset").headers["location"] == "/users?err=not_found"
    assert (
        post_form(site, "/users/999/active", value="0").headers["location"]
        == "/users?err=not_found"
    )
    with open_session(site) as s, s.begin():
        _make(s, "Berta")
    uid = _user_id(site, "Berta")
    assert post_form(site, f"/users/{uid}/reset", csrf=False).status_code == 403
    assert (
        post_form(
            site, f"/users/{uid}/active", headers={"origin": "https://boese.example"}, value="0"
        ).status_code
        == 403
    )
    with open_session(site) as s:
        assert s.get_one(AppUser, uid).is_active is True


def test_anonymous_users_are_sent_to_the_login(anon: TestClient) -> None:
    r = anon.get("/users", follow_redirects=False)
    assert (r.status_code, r.headers["location"].startswith("/login")) == (303, True)
    assert anon.post("/users", data={"username": "x"}, follow_redirects=False).status_code == 401
