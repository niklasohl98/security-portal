"""Anmeldung, Sitzungen und Benutzer (lokale Benutzerdatenbank, ADR 0007).

Sicherheitsmerkmale: Argon2id-Hash; immer dieselbe Antwort bei unbekanntem Benutzer, falschem
Passwort und Sperre (kein Erraten von Benutzernamen); Rechenzeit auch für unbekannte Benutzer;
Sperre nach ``login_max_attempts`` Fehlversuchen für ``login_lockout_minutes``; Sitzungstoken nur
als SHA-256-Hash gespeichert, bei jeder Anmeldung neu; ein Passwortwechsel beendet alle anderen
Sitzungen. Das Audit-Log enthält nie Passwörter oder fremde Eingaben: bei unbekannten Benutzern
gibt es keinen Datenbankeintrag (nur eine feste Logzeile), sonst nur die Benutzer-ID.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import passwords
from app.auth.bootstrap import INITIAL_PASSWORD, INITIAL_USERNAME
from app.config import Settings
from app.db.models import AppSession, AppUser, AuditLog, utcnow

log = logging.getLogger(__name__)

_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
ACTOR_SYSTEM = "system"


class AuthOutcome(StrEnum):
    """Ergebnis einer Anmeldung. Nach außen sehen ``INVALID`` und ``LOCKED`` gleich aus."""

    OK = "ok"
    INVALID = "invalid"
    LOCKED = "locked"


@dataclass(frozen=True)
class AuthResult:
    """Ergebnis von ``authenticate``."""

    outcome: AuthOutcome
    user_id: int | None = None
    username: str | None = None
    must_change: bool = False


@dataclass(frozen=True)
class NewSession:
    """Eine neue Sitzung: das Token gehört nur ins Cookie und wird nicht gespeichert."""

    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionInfo:
    """Eine gültige Sitzung mit Benutzer (losgelöst von der Datenbanksitzung)."""

    session_id: int
    user_id: int
    username: str
    must_change: bool
    csrf_token: str
    is_admin: bool = False
    is_oidc: bool = False


class PasswordChangeError(Exception):
    """Neues Passwort für ``create_user``/``reset_password`` abgelehnt; ``codes`` sind feste Texte
    (siehe ``passwords.check_new_password``)."""

    def __init__(self, codes: list[str]) -> None:
        super().__init__(",".join(codes))
        self.codes = codes


class UserError(Exception):
    """Benutzer nicht anlegbar oder gefunden. ``code``: ``bad_name``, ``exists``, ``not_found``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def username_key(username: str) -> str:
    """Schlüssel für Eindeutigkeit und Suche (ohne Leerraum am Rand, kleingeschrieben)."""
    return username.strip().casefold()


def _audit(session: Session, at: datetime, actor: str, action: str, detail: str) -> None:
    session.add(AuditLog(tenant_id=None, at=at, actor=actor, action=action, detail=detail))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _find_user(session: Session, username: str) -> AppUser | None:
    return session.scalars(
        select(AppUser).where(AppUser.username_key == username_key(username))
    ).one_or_none()


def _register_failure(session: Session, user: AppUser, settings: Settings, now: datetime) -> None:
    """Zählt einen Fehlversuch und sperrt das Konto bei Erreichen der Grenze."""
    user.failed_attempts += 1
    _audit(session, now, f"user:{user.id}", "auth.login_failed", f"user_id={user.id}")
    if user.failed_attempts >= settings.login_max_attempts:
        user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
        user.failed_attempts = 0
        _audit(session, now, f"user:{user.id}", "auth.lockout", f"user_id={user.id}")
        log.warning("Konto gesperrt user_id=%s minuten=%s", user.id, settings.login_lockout_minutes)


def authenticate(
    session: Session, settings: Settings, username: str, password: str, now: datetime | None = None
) -> AuthResult:
    """Prüft Benutzername und Passwort. Der Aufrufer übernimmt den Commit."""
    now = now or utcnow()
    user = _find_user(session, username)
    if user is None or not user.is_active or user.oidc_object_id is not None:
        passwords.burn_time(password)
        log.warning("Anmeldung fehlgeschlagen: unbekannter oder deaktivierter Benutzer")
        return AuthResult(AuthOutcome.INVALID)
    if user.locked_until is not None and user.locked_until > now:
        passwords.burn_time(password)
        return AuthResult(AuthOutcome.LOCKED)
    if not passwords.verify_password(user.password_hash, password):
        _register_failure(session, user, settings, now)
        return AuthResult(AuthOutcome.INVALID)
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    _audit(session, now, user.username, "auth.login", f"user_id={user.id}")
    return AuthResult(AuthOutcome.OK, user.id, user.username, user.must_change_password)


def create_session(
    session: Session,
    settings: Settings,
    user_id: int,
    now: datetime | None = None,
    *,
    is_oidc: bool = False,
) -> NewSession:
    """Legt eine Sitzung an (neues Token bei jeder Anmeldung) und räumt abgelaufene auf."""
    now = now or utcnow()
    session.execute(delete(AppSession).where(AppSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=settings.session_hours)
    session.add(
        AppSession(
            user_id=user_id,
            token_hash=_hash_token(token),
            csrf_token=csrf,
            created_at=now,
            expires_at=expires,
            is_oidc=is_oidc,
        )
    )
    session.flush()
    return NewSession(token, csrf, expires)


def load_session(session: Session, token: str, now: datetime | None = None) -> SessionInfo | None:
    """Die gültige Sitzung zum Cookie-Token oder ``None`` (unbekannt, abgelaufen, inaktiv)."""
    if not token:
        return None
    now = now or utcnow()
    row = session.execute(
        select(AppSession, AppUser)
        .join(AppUser, AppUser.id == AppSession.user_id)
        .where(AppSession.token_hash == _hash_token(token))
    ).first()
    if row is None:
        return None
    app_session, user = row
    if (
        app_session.expires_at <= now
        or not user.is_active
        or app_session.is_oidc != (user.oidc_object_id is not None)
    ):
        return None
    return SessionInfo(
        app_session.id,
        user.id,
        user.username,
        user.must_change_password,
        app_session.csrf_token,
        user.is_admin,
        app_session.is_oidc,
    )


def destroy_session(session: Session, token: str) -> None:
    """Beendet die Sitzung zu diesem Token (Abmelden)."""
    session.execute(delete(AppSession).where(AppSession.token_hash == _hash_token(token)))


def destroy_user_sessions(
    session: Session, user_id: int, keep_session_id: int | None = None
) -> int:
    """Beendet alle Sitzungen eines Benutzers außer ``keep_session_id``; gibt die Anzahl zurück."""
    stmt = delete(AppSession).where(AppSession.user_id == user_id)
    if keep_session_id is not None:
        stmt = stmt.where(AppSession.id != keep_session_id)
    return session.execute(stmt).rowcount  # type: ignore[attr-defined, no-any-return]


def record_logout(session: Session, info: SessionInfo, now: datetime | None = None) -> None:
    """Schreibt das Abmelden ins Audit-Log."""
    _audit(session, now or utcnow(), info.username, "auth.logout", f"user_id={info.user_id}")


def change_password(
    session: Session,
    settings: Settings,
    user_id: int,
    current: str,
    new: str,
    confirm: str,
    keep_session_id: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Ändert das Passwort und hebt den Zwang zum Wechsel auf. Der Aufrufer übernimmt den Commit.

    Gibt die Fehlercodes zurück (leer = geändert): ``wrong_current``, ``locked``,
    ``same_as_current`` und die Codes aus ``passwords.check_new_password``. Es wird bewusst nichts
    geworfen: Der Zähler für Fehlversuche (falsches aktuelles Passwort zählt wie beim Login) muss
    auch im Fehlerfall gespeichert werden, der Aufrufer committet also immer.
    """
    now = now or utcnow()
    user = session.get(AppUser, user_id)
    if user is None or not user.is_active or user.oidc_object_id is not None:
        return ["wrong_current"]
    if user.locked_until is not None and user.locked_until > now:
        return ["locked"]
    if not passwords.verify_password(user.password_hash, current):
        _register_failure(session, user, settings, now)
        return ["wrong_current"]
    codes = passwords.check_new_password(
        new, confirm, username=user.username, forbidden=[INITIAL_PASSWORD]
    )
    if new == current:
        codes.append("same_as_current")
    if codes:
        return codes
    user.password_hash = passwords.hash_password(new)
    user.must_change_password = False
    user.password_changed_at = now
    user.failed_attempts = 0
    user.locked_until = None
    destroy_user_sessions(session, user.id, keep_session_id)
    _audit(session, now, user.username, "auth.password_change", f"user_id={user.id}")
    log.info("Passwort geändert user_id=%s", user.id)
    return []


def ensure_initial_admin(session: Session, now: datetime | None = None) -> bool:
    """Legt ``Admin`` an, wenn es noch keinen Benutzer gibt. ``True`` = angelegt."""
    if (session.scalar(select(func.count()).select_from(AppUser)) or 0) > 0:
        return False
    now = now or utcnow()
    user = AppUser(
        username=INITIAL_USERNAME,
        username_key=username_key(INITIAL_USERNAME),
        password_hash=passwords.hash_password(INITIAL_PASSWORD),
        must_change_password=True,
        is_admin=True,
        created_at=now,
    )
    session.add(user)
    session.flush()
    _audit(session, now, ACTOR_SYSTEM, "user.create", f"user_id={user.id} initial=ja")
    log.warning(
        "Initialkonto angelegt; das Startpasswort muss bei der ersten Anmeldung geändert werden"
    )
    return True


def create_user(
    session: Session,
    username: str,
    password: str,
    actor: str,
    now: datetime | None = None,
    is_admin: bool = False,
) -> int:
    """Legt einen Benutzer an (Passwort muss beim ersten Login geändert werden).

    Raises:
        UserError: ``bad_name`` oder ``exists``.
        PasswordChangeError: Passwort verletzt die Regeln (ohne ``wrong_current``).
    """
    now = now or utcnow()
    name = username.strip()
    if not _USERNAME.fullmatch(name):
        raise UserError("bad_name")
    if _find_user(session, name) is not None:
        raise UserError("exists")
    codes = passwords.check_new_password(
        password, password, username=name, forbidden=[INITIAL_PASSWORD]
    )
    if codes:
        raise PasswordChangeError(codes)
    user = AppUser(
        username=name,
        username_key=username_key(name),
        password_hash=passwords.hash_password(password),
        must_change_password=True,
        is_admin=is_admin,
        created_at=now,
    )
    session.add(user)
    session.flush()
    _audit(session, now, actor, "user.create", f"user_id={user.id}")
    return user.id


def reset_password(
    session: Session, username: str, password: str, actor: str, now: datetime | None = None
) -> int:
    """Setzt ein Passwort zurück (Wechsel beim nächsten Login erzwungen, Sperre und Sitzungen weg).

    Raises:
        UserError: ``not_found``.
        PasswordChangeError: Passwort verletzt die Regeln.
    """
    now = now or utcnow()
    user = _find_user(session, username)
    if user is None:
        raise UserError("not_found")
    if user.oidc_object_id is not None:
        raise UserError("external")
    codes = passwords.check_new_password(
        password, password, username=user.username, forbidden=[INITIAL_PASSWORD]
    )
    if codes:
        raise PasswordChangeError(codes)
    user.password_hash = passwords.hash_password(password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    user.is_active = True
    destroy_user_sessions(session, user.id)
    _audit(session, now, actor, "user.reset_password", f"user_id={user.id}")
    return user.id


def list_users(session: Session) -> list[AppUser]:
    """Alle Benutzer nach Name (für die CLI)."""
    return list(session.scalars(select(AppUser).order_by(AppUser.username_key)))
