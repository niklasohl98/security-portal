"""Ausnahmen von der Inaktivitätsprüfung (z. B. Break-Glass, Dienstkonten).

Eine Ausnahme braucht Grund und Ablaufdatum (Standardlaufzeit je Mandant, ``exception_days``), damit
sie regelmäßig neu bestätigt wird. Je Identität gibt es höchstens eine gültige Ausnahme. Jede
Änderung steht im Audit-Log (ohne Namen) und stößt die Auswertung des Mandanten an, damit Status und
Flag sofort stimmen. Ausnahmen betreffen nur dieses Tool, nie Entra oder AD.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.identities import refresh_identities, refresh_lock
from app.db.models import AccountException, AuditLog, Identity, Tenant, utcnow

log = logging.getLogger(__name__)

MAX_REASON_LENGTH = 500


class ExceptionError(Exception):
    """Ausnahme nicht möglich. ``code`` ist ein fester Text ohne Kontoinhalte."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExceptionItem:
    """Eine Ausnahme für die Anzeige."""

    exception_id: int
    identity_id: int
    display_name: str | None
    reason: str
    created_by: str
    created_at: datetime
    valid_until: datetime
    expired: bool
    expires_soon: bool  # gültig, läuft aber innerhalb der Vorwarnzeit ab


def default_valid_until(tenant: Tenant, now: datetime | None = None) -> datetime:
    """Standardablauf: jetzt plus ``exception_days`` des Mandanten."""
    return (now or utcnow()) + timedelta(days=tenant.exception_days)


def _audit(
    session: Session, tenant: Tenant, actor: str, action: str, detail: str, now: datetime
) -> None:
    session.add(AuditLog(tenant_id=tenant.id, at=now, actor=actor, action=action, detail=detail))


def create_exception(
    session: Session,
    tenant: Tenant,
    identity_id: int,
    reason: str,
    valid_until: datetime,
    actor: str,
    now: datetime | None = None,
) -> AccountException:
    """Legt eine Ausnahme an und wertet den Mandanten neu aus. Commit macht der Aufrufer.

    Raises:
        ExceptionError: ``not_found`` (Identität gehört nicht zu diesem Mandanten),
            ``reason_required``, ``reason_too_long``, ``not_in_future``, ``already_valid``.
    """
    now = now or utcnow()
    reason = reason.strip()
    if not reason:
        raise ExceptionError("reason_required")
    if len(reason) > MAX_REASON_LENGTH:
        raise ExceptionError("reason_too_long")
    if valid_until.tzinfo is None:
        raise ValueError("valid_until muss timezone-aware sein")
    if valid_until <= now:
        raise ExceptionError("not_in_future")
    identity = session.scalars(
        select(Identity).where(Identity.id == identity_id, Identity.tenant_id == tenant.id)
    ).one_or_none()
    if identity is None:
        raise ExceptionError("not_found")
    existing = session.scalars(
        select(AccountException).where(
            AccountException.tenant_id == tenant.id,
            AccountException.identity_id == identity_id,
            AccountException.valid_until > now,
        )
    ).first()
    if existing is not None:
        raise ExceptionError("already_valid")

    row = AccountException(
        tenant_id=tenant.id,
        identity_id=identity_id,
        reason=reason,
        created_by=actor,
        created_at=now,
        valid_until=valid_until,
    )
    session.add(row)
    session.flush()
    until = valid_until.date().isoformat()
    detail = f"exception_id={row.id} identity_id={identity_id} gueltig_bis={until}"
    _audit(session, tenant, actor, "exception.create", detail, now)
    _refresh(session, tenant, now)
    log.info("Ausnahme angelegt tenant_id=%s exception_id=%s", tenant.id, row.id)
    return row


def revoke_exception(
    session: Session, tenant: Tenant, exception_id: int, actor: str, now: datetime | None = None
) -> None:
    """Löscht eine Ausnahme (gültig oder abgelaufen) und wertet neu aus. Commit macht der Aufrufer.

    Raises:
        ExceptionError: ``not_found`` (gibt es bei diesem Mandanten nicht).
    """
    now = now or utcnow()
    row = session.scalars(
        select(AccountException).where(
            AccountException.id == exception_id, AccountException.tenant_id == tenant.id
        )
    ).one_or_none()
    if row is None:
        raise ExceptionError("not_found")
    detail = f"exception_id={row.id} identity_id={row.identity_id}"
    session.delete(row)
    session.flush()
    _audit(session, tenant, actor, "exception.delete", detail, now)
    _refresh(session, tenant, now)
    log.info("Ausnahme gelöscht tenant_id=%s exception_id=%s", tenant.id, exception_id)


def _refresh(session: Session, tenant: Tenant, now: datetime) -> None:
    with refresh_lock(tenant.id):
        refresh_identities(session, tenant, now)


def list_exceptions(
    session: Session, tenant: Tenant, now: datetime | None = None
) -> list[ExceptionItem]:
    """Alle Ausnahmen des Mandanten, nach Ablaufdatum aufsteigend (Dringendstes zuerst)."""
    now = now or utcnow()
    warn_until = now + timedelta(days=tenant.exception_warn_days)
    rows = session.execute(
        select(AccountException, Identity.display_name)
        .join(Identity, Identity.id == AccountException.identity_id)
        .where(AccountException.tenant_id == tenant.id, Identity.tenant_id == tenant.id)
        .order_by(AccountException.valid_until, AccountException.id)
    ).all()
    return [
        ExceptionItem(
            exception_id=ex.id,
            identity_id=ex.identity_id,
            display_name=name,
            reason=ex.reason,
            created_by=ex.created_by,
            created_at=ex.created_at,
            valid_until=ex.valid_until,
            expired=ex.valid_until <= now,
            expires_soon=now < ex.valid_until <= warn_until,
        )
        for ex, name in rows
    ]
