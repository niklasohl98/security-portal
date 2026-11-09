"""Benutzerverwaltung durch Admins: anlegen, deaktivieren, Rolle ändern, Passwort zurücksetzen.

Passwörter wählt der Admin nie selbst: Anlegen und Zurücksetzen erzeugen ein **Einmalpasswort**, das
nur in der Antwort erscheint (nie in Datenbank, Log oder Audit-Log) und beim ersten Login geändert
werden muss. Schutzregeln: Man verwaltet sich nicht selbst (eigenes Passwort über ``/password``) und
der letzte aktive Admin kann weder deaktiviert noch herabgestuft werden, damit die Verwaltung nie
unerreichbar wird. Jede Änderung steht im Audit-Log (nur Benutzer-IDs).
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.passwords import check_new_password
from app.db.models import AppUser, AuditLog, utcnow

log = logging.getLogger(__name__)

# Ohne leicht verwechselbare Zeichen (0/O, 1/l/I), damit sich das Passwort abtippen lässt.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
TEMP_PASSWORD_LENGTH = 16


class UserAdminError(Exception):
    """Verwaltungsaktion nicht möglich. ``code``: ``not_found``, ``self``, ``last_admin``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class UserRow:
    """Ein Benutzer für die Liste (ohne Hash)."""

    id: int
    username: str
    is_admin: bool
    is_active: bool
    must_change: bool
    locked: bool
    last_login_at: datetime | None
    created_at: datetime
    is_oidc: bool = False


def generate_temp_password(username: str = "") -> str:
    """Zufälliges Einmalpasswort (16 Zeichen), das die Passwortregeln erfüllt."""
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))
        if not check_new_password(candidate, candidate, username=username):
            return candidate


def _audit(session: Session, now: datetime, actor: str, action: str, user_id: int) -> None:
    session.add(
        AuditLog(tenant_id=None, at=now, actor=actor, action=action, detail=f"user_id={user_id}")
    )


def _get(session: Session, user_id: int) -> AppUser:
    user = session.get(AppUser, user_id)
    if user is None:
        raise UserAdminError("not_found")
    return user


def _other_active_admins(session: Session, user_id: int) -> int:
    return (
        session.scalar(
            select(func.count()).where(
                AppUser.is_admin.is_(True),
                AppUser.is_active.is_(True),
                AppUser.id != user_id,
                AppUser.oidc_object_id.is_(None),
            )
        )
        or 0
    )


def list_user_rows(session: Session, now: datetime | None = None) -> list[UserRow]:
    """Alle Benutzer nach Name."""
    now = now or utcnow()
    return [
        UserRow(
            id=u.id,
            username=u.username,
            is_admin=u.is_admin,
            is_active=u.is_active,
            must_change=u.must_change_password,
            locked=u.locked_until is not None and u.locked_until > now,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            is_oidc=u.oidc_object_id is not None,
        )
        for u in service.list_users(session)
    ]


def create_with_temp_password(
    session: Session, username: str, is_admin: bool, actor: str, now: datetime | None = None
) -> tuple[int, str]:
    """Legt einen Benutzer mit Einmalpasswort an. Gibt ID und Passwort zurück (nur jetzt sichtbar).

    Raises:
        service.UserError: ``bad_name`` oder ``exists``.
    """
    name = username.strip()
    password = generate_temp_password(name)
    user_id = service.create_user(session, name, password, actor, now, is_admin=is_admin)
    return user_id, password


def reset_with_temp_password(
    session: Session, user_id: int, actor_id: int, actor: str, now: datetime | None = None
) -> tuple[str, str]:
    """Setzt das Passwort eines **anderen** Benutzers auf ein Einmalpasswort zurück.

    Hebt Sperre und Deaktivierung auf (wie ``service.reset_password``) und beendet seine Sitzungen.
    Gibt Benutzername und Passwort zurück.

    Raises:
        UserAdminError: ``not_found`` oder ``self``.
    """
    user = _get(session, user_id)
    if user.id == actor_id:
        raise UserAdminError("self")
    if user.oidc_object_id is not None:
        raise UserAdminError("external")
    password = generate_temp_password(user.username)
    service.reset_password(session, user.username, password, actor, now)
    return user.username, password


def set_active(
    session: Session,
    user_id: int,
    active: bool,
    actor_id: int,
    actor: str,
    now: datetime | None = None,
) -> None:
    """Aktiviert oder deaktiviert einen anderen Benutzer; Deaktivieren beendet seine Sitzungen.

    Raises:
        UserAdminError: ``not_found``, ``self`` oder ``last_admin``.
    """
    now = now or utcnow()
    user = _get(session, user_id)
    if user.id == actor_id:
        raise UserAdminError("self")
    if (
        not active
        and user.is_admin
        and user.is_active
        and _other_active_admins(session, user.id) < 1
    ):
        raise UserAdminError("last_admin")
    if user.is_active == active:
        return
    user.is_active = active
    if not active:
        service.destroy_user_sessions(session, user.id)
    _audit(session, now, actor, "user.activate" if active else "user.deactivate", user.id)
    session.flush()


def set_admin(
    session: Session,
    user_id: int,
    admin: bool,
    actor_id: int,
    actor: str,
    now: datetime | None = None,
) -> None:
    """Gibt einem anderen Benutzer die Admin-Rolle oder nimmt sie ihm.

    Raises:
        UserAdminError: ``not_found``, ``self`` oder ``last_admin``.
    """
    now = now or utcnow()
    user = _get(session, user_id)
    if user.id == actor_id:
        raise UserAdminError("self")
    if user.oidc_object_id is not None:
        raise UserAdminError("external")
    if (
        not admin
        and user.is_admin
        and user.is_active
        and _other_active_admins(session, user.id) < 1
    ):
        raise UserAdminError("last_admin")
    if user.is_admin == admin:
        return
    user.is_admin = admin
    _audit(session, now, actor, "user.admin_on" if admin else "user.admin_off", user.id)
    session.flush()
