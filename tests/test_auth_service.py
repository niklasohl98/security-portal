from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import service
from app.auth.bootstrap import INITIAL_PASSWORD, INITIAL_USERNAME
from app.auth.service import AuthOutcome, PasswordChangeError, UserError
from app.config import Settings
from app.db.models import AppSession, AppUser, AuditLog
from tests.identity_fakes import NOW

GOOD = "Ein-gutes-Passwort-1"


def _user(s: Session, name: str = "Anna", password: str = GOOD, *, change: bool = False) -> int:
    uid = service.create_user(s, name, password, "test", NOW)
    s.get_one(AppUser, uid).must_change_password = change
    return uid


def _actions(s: Session) -> list[str]:
    return [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_initial_admin_is_created_once_and_forces_a_change(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        assert service.ensure_initial_admin(s, NOW) is True
    with factory.begin() as s:
        assert service.ensure_initial_admin(s, NOW) is False  # nur bei leerer Tabelle
        (user,) = s.scalars(select(AppUser)).all()
        assert (user.username, user.must_change_password, user.is_active) == (
            INITIAL_USERNAME,
            True,
            True,
        )
        assert user.password_hash.startswith("$argon2id$")
        assert INITIAL_PASSWORD not in user.password_hash  # nie im Klartext
        (entry,) = s.scalars(select(AuditLog)).all()
        assert (entry.action, entry.actor) == ("user.create", "system")


def test_initial_admin_is_not_recreated_after_the_user_changed_the_name_or_data(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        _user(s, "Andere")
    with factory.begin() as s:
        assert service.ensure_initial_admin(s, NOW) is False
        assert [u.username for u in s.scalars(select(AppUser))] == ["Andere"]


def test_the_initial_login_works_but_requires_a_password_change(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        service.ensure_initial_admin(s, NOW)
    with factory.begin() as s:
        result = service.authenticate(s, settings, "Admin", "Passwort123", NOW)
        assert result.outcome is AuthOutcome.OK
        assert result.must_change is True
        assert (
            service.authenticate(s, settings, "admin", "Passwort123", NOW).outcome is AuthOutcome.OK
        )


def test_successful_login_resets_counters_and_is_audited_without_the_name(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        s.get_one(AppUser, uid).failed_attempts = 3
    with factory.begin() as s:
        result = service.authenticate(
            s, settings, " ANNA ", GOOD, NOW
        )  # Groß-/Kleinschreibung egal
        assert (result.outcome, result.user_id, result.username) == (AuthOutcome.OK, uid, "Anna")
    with factory() as s:
        user = s.get_one(AppUser, uid)
        assert (user.failed_attempts, user.last_login_at, user.locked_until) == (0, NOW, None)
        login = s.scalars(select(AuditLog).where(AuditLog.action == "auth.login")).one()
        assert login.detail == f"user_id={uid}"


def test_wrong_password_counts_up_and_is_audited_with_the_id_only(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
    with factory.begin() as s:
        assert (
            service.authenticate(s, settings, "Anna", "falsch-falsch-1", NOW).outcome
            is AuthOutcome.INVALID
        )
    with factory() as s:
        assert s.get_one(AppUser, uid).failed_attempts == 1
        failed = s.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")).one()
        assert failed.detail == f"user_id={uid}"
        assert "falsch" not in (failed.detail or "")
        assert failed.actor == f"user:{uid}"


def test_unknown_user_is_invalid_and_leaves_no_database_trace(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        _user(s)
    with factory.begin() as s:
        r = service.authenticate(s, settings, "<script>x</script>", "egal-egal-egal", NOW)
        assert r.outcome is AuthOutcome.INVALID
    with factory() as s:
        assert _actions(s) == ["user.create"]  # kein Eintrag für den unbekannten Namen


def test_account_is_locked_after_the_limit_even_for_the_correct_password(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
    for _ in range(settings.login_max_attempts):
        with factory.begin() as s:
            assert (
                service.authenticate(s, settings, "Anna", "falsch-falsch-1", NOW).outcome
                is AuthOutcome.INVALID
            )
    with factory.begin() as s:
        assert service.authenticate(s, settings, "Anna", GOOD, NOW).outcome is AuthOutcome.LOCKED
    with factory() as s:
        user = s.get_one(AppUser, uid)
        assert user.locked_until == NOW + timedelta(minutes=settings.login_lockout_minutes)
        assert "auth.lockout" in _actions(s)
    later = NOW + timedelta(minutes=settings.login_lockout_minutes, seconds=1)
    with factory.begin() as s:
        assert service.authenticate(s, settings, "Anna", GOOD, later).outcome is AuthOutcome.OK


def test_the_limit_comes_from_the_settings(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    strict = replace(settings, login_max_attempts=2, login_lockout_minutes=1)
    with factory.begin() as s:
        _user(s)
    for _ in range(2):
        with factory.begin() as s:
            service.authenticate(s, strict, "Anna", "falsch-falsch-1", NOW)
    with factory.begin() as s:
        assert service.authenticate(s, strict, "Anna", GOOD, NOW).outcome is AuthOutcome.LOCKED
        later = NOW + timedelta(minutes=1, seconds=1)
        assert service.authenticate(s, strict, "Anna", GOOD, later).outcome is AuthOutcome.OK


def test_inactive_user_cannot_log_in(factory: sessionmaker[Session], settings: Settings) -> None:
    with factory.begin() as s:
        s.get_one(AppUser, _user(s)).is_active = False
    with factory.begin() as s:
        assert service.authenticate(s, settings, "Anna", GOOD, NOW).outcome is AuthOutcome.INVALID


def test_session_roundtrip_and_token_is_not_stored(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        new = service.create_session(s, settings, uid, NOW)
    with factory() as s:
        info = service.load_session(s, new.token, NOW + timedelta(hours=1))
        assert info is not None
        assert (info.user_id, info.username, info.must_change) == (uid, "Anna", False)
        assert info.csrf_token == new.csrf_token
        row = s.scalars(select(AppSession)).one()
        assert new.token not in (
            row.token_hash,
            row.csrf_token,
        )  # nur der Hash liegt in der Datenbank
        assert len(row.token_hash) == 64
        assert new.expires_at == NOW + timedelta(hours=settings.session_hours)


def test_every_login_gets_a_new_unpredictable_token(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        a, b = (
            service.create_session(s, settings, uid, NOW),
            service.create_session(s, settings, uid, NOW),
        )
    assert a.token != b.token
    assert a.csrf_token != b.csrf_token
    assert len(a.token) >= 40


@pytest.mark.parametrize("token", ["", "unbekannt", "a" * 500])
def test_unknown_tokens_load_nothing(factory: sessionmaker[Session], token: str) -> None:
    with factory() as s:
        assert service.load_session(s, token, NOW) is None


def test_expired_sessions_and_inactive_users_do_not_load(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        new = service.create_session(s, settings, uid, NOW)
    with factory() as s:
        assert service.load_session(s, new.token, new.expires_at - timedelta(seconds=1)) is not None
        assert service.load_session(s, new.token, new.expires_at) is None  # genau zum Ablauf
    with factory.begin() as s:
        s.get_one(AppUser, uid).is_active = False
    with factory() as s:
        assert service.load_session(s, new.token, NOW) is None


def test_new_sessions_clean_up_expired_ones(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        service.create_session(s, settings, uid, NOW)
    later = NOW + timedelta(hours=settings.session_hours, minutes=1)
    with factory.begin() as s:
        service.create_session(s, settings, uid, later)
        assert len(s.scalars(select(AppSession)).all()) == 1


def test_destroy_session_and_user_sessions(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        a, b, c = (service.create_session(s, settings, uid, NOW) for _ in range(3))
        info_b = service.load_session(s, b.token, NOW)
        assert info_b is not None
    with factory.begin() as s:
        service.destroy_session(s, a.token)
    with factory.begin() as s:
        assert service.load_session(s, a.token, NOW) is None
        assert service.destroy_user_sessions(s, uid, keep_session_id=info_b.session_id) == 1
    with factory() as s:
        assert service.load_session(s, b.token, NOW) is not None
        assert service.load_session(s, c.token, NOW) is None


def test_change_password_success_ends_other_sessions_and_the_forced_change(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s, change=True)
        mine, other = (
            service.create_session(s, settings, uid, NOW),
            service.create_session(s, settings, uid, NOW),
        )
        info = service.load_session(s, mine.token, NOW)
        assert info is not None
    new_password = "Ganz-neues-Passwort-9"
    with factory.begin() as s:
        assert (
            service.change_password(
                s, settings, uid, GOOD, new_password, new_password, info.session_id, NOW
            )
            == []
        )
    with factory() as s:
        user = s.get_one(AppUser, uid)
        assert (user.must_change_password, user.password_changed_at) == (False, NOW)
        assert service.load_session(s, mine.token, NOW) is not None  # die eigene bleibt
        assert service.load_session(s, other.token, NOW) is None  # alle anderen enden
        assert "auth.password_change" in _actions(s)
    with factory.begin() as s:
        assert (
            service.authenticate(s, settings, "Anna", new_password, NOW).outcome is AuthOutcome.OK
        )
        assert service.authenticate(s, settings, "Anna", GOOD, NOW).outcome is AuthOutcome.INVALID


@pytest.mark.parametrize(
    ("new", "confirm", "expected"),
    [
        ("Ganz-neues-Passwort-9", "Anderes-Passwort-99", ["mismatch"]),
        ("kurz", "kurz", ["too_short"]),
        ("Passwort123", "Passwort123", ["too_short", "forbidden"]),
        (GOOD, GOOD, ["same_as_current"]),
        ("Anna-und-mehr-12345", "Anna-und-mehr-12345", ["contains_username"]),
    ],
)
def test_change_password_rejects_weak_or_unchanged_passwords(
    factory: sessionmaker[Session], settings: Settings, new: str, confirm: str, expected: list[str]
) -> None:
    with factory.begin() as s:
        uid = _user(s, change=True)
    with factory.begin() as s:
        assert service.change_password(s, settings, uid, GOOD, new, confirm, None, NOW) == expected
    with factory() as s:
        assert s.get_one(AppUser, uid).must_change_password is True  # unverändert
        assert "auth.password_change" not in _actions(s)


def test_wrong_current_password_counts_and_locks_like_a_login(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
    for _ in range(settings.login_max_attempts):
        with factory.begin() as s:  # Commit auch im Fehlerfall: der Zähler muss ankommen
            assert service.change_password(
                s,
                settings,
                uid,
                "falsch-falsch-1",
                "Neu-und-lang-123",
                "Neu-und-lang-123",
                None,
                NOW,
            ) == ["wrong_current"]
    with factory.begin() as s:
        assert service.change_password(
            s, settings, uid, GOOD, "Neu-und-lang-123", "Neu-und-lang-123", None, NOW
        ) == ["locked"]
        assert s.get_one(AppUser, uid).locked_until is not None


def test_change_password_for_missing_or_inactive_user(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        s.get_one(AppUser, uid).is_active = False
    with factory.begin() as s:
        assert service.change_password(
            s, settings, uid, GOOD, "Neu-und-lang-123", "Neu-und-lang-123", None, NOW
        ) == ["wrong_current"]
        assert service.change_password(
            s, settings, 9999, GOOD, "Neu-und-lang-123", "Neu-und-lang-123", None, NOW
        ) == ["wrong_current"]


@pytest.mark.parametrize("name", ["ab", "a b", "../x", "", "x" * 65, "-anna", "an@na"])
def test_create_user_rejects_bad_names(factory: sessionmaker[Session], name: str) -> None:
    with factory() as s, pytest.raises(UserError) as err:
        service.create_user(s, name, GOOD, "test", NOW)
    assert err.value.code == "bad_name"


def test_create_user_rejects_duplicates_case_insensitively_and_weak_passwords(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        _user(s, "Anna")
    with factory() as s, pytest.raises(UserError) as err:
        service.create_user(s, "ANNA", GOOD, "test", NOW)
    assert err.value.code == "exists"
    with factory() as s, pytest.raises(PasswordChangeError) as weak:
        service.create_user(s, "Berta", "zu-kurz", "test", NOW)
    assert weak.value.codes == ["too_short"]
    with factory() as s:
        assert [u.username for u in s.scalars(select(AppUser))] == ["Anna"]


def test_created_users_must_change_their_password_and_are_audited(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        uid = service.create_user(s, "Berta", GOOD, "cli", NOW)
    with factory() as s:
        assert s.get_one(AppUser, uid).must_change_password is True
        entry = s.scalars(select(AuditLog)).one()
        assert (entry.action, entry.actor, entry.detail) == ("user.create", "cli", f"user_id={uid}")


def test_reset_password_unlocks_activates_and_ends_sessions(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        uid = _user(s)
        user = s.get_one(AppUser, uid)
        user.is_active, user.failed_attempts, user.locked_until = False, 2, NOW + timedelta(hours=1)
        sess = service.create_session(s, settings, uid, NOW)
    with factory.begin() as s:
        service.reset_password(s, "anna", "Neues-Startpasswort-7", "cli", NOW)
    with factory() as s:
        user = s.get_one(AppUser, uid)
        assert (
            user.must_change_password,
            user.is_active,
            user.failed_attempts,
            user.locked_until,
        ) == (True, True, 0, None)
        assert service.load_session(s, sess.token, NOW) is None
        assert "user.reset_password" in _actions(s)
    with factory.begin() as s:
        assert (
            service.authenticate(s, settings, "Anna", "Neues-Startpasswort-7", NOW).outcome
            is AuthOutcome.OK
        )


def test_reset_password_errors(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        _user(s)
    with factory() as s, pytest.raises(UserError) as err:
        service.reset_password(s, "gibtsnicht", GOOD, "cli", NOW)
    assert err.value.code == "not_found"
    with factory() as s, pytest.raises(PasswordChangeError):
        service.reset_password(s, "Anna", "Passwort123", "cli", NOW)


def test_list_users_is_sorted(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        _user(s, "Berta")
        _user(s, "anna")
        _user(s, "Carl")
    with factory() as s:
        assert [u.username for u in service.list_users(s)] == ["anna", "Berta", "Carl"]


def test_login_rehashes_old_parameters(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argon2 import PasswordHasher

    from app.auth import passwords

    with factory.begin() as s:
        uid = _user(s)
        old_hash = s.get_one(AppUser, uid).password_hash
    monkeypatch.setattr(
        passwords, "_hasher", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )
    with factory.begin() as s:
        assert service.authenticate(s, settings, "Anna", GOOD, NOW).outcome is AuthOutcome.OK
    with factory() as s:
        assert s.get_one(AppUser, uid).password_hash != old_hash
    with factory.begin() as s:
        assert (
            service.authenticate(s, settings, "Anna", GOOD, NOW).outcome is AuthOutcome.OK
        )  # neuer Hash gilt
