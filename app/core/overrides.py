"""Manuelle Kontoart einer Identität setzen oder zurücknehmen.

Die manuelle Kontoart ersetzt die automatische (Namensmuster) für Anzeige und Empfehlung und
bleibt über Läufe erhalten. Jede Änderung steht im Audit-Log (nur IDs und der Kontoart-Code) und
stößt die Auswertung des Mandanten an.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.identities import refresh_identities, refresh_lock
from app.core.rules import AccountType
from app.db.models import AuditLog, Identity, Tenant, utcnow

log = logging.getLogger(__name__)


class OverrideError(Exception):
    """Änderung nicht möglich. ``code`` ist ein fester Text: ``not_found`` oder ``bad_type``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def set_account_type(
    session: Session,
    tenant: Tenant,
    identity_id: int,
    account_type: str | None,
    actor: str,
    now: datetime | None = None,
) -> None:
    """Setzt die manuelle Kontoart (``None`` oder leer = zurück zur automatischen).

    Der Aufrufer übernimmt den Commit.

    Raises:
        OverrideError: ``bad_type`` (unbekannte Kontoart), ``not_found`` (Identität gehört nicht
            zu diesem Mandanten oder ist verschwunden).
    """
    now = now or utcnow()
    value = (account_type or "").strip() or None
    if value is not None:
        try:
            AccountType(value)
        except ValueError:
            raise OverrideError("bad_type") from None
    row = session.scalars(
        select(Identity).where(
            Identity.id == identity_id,
            Identity.tenant_id == tenant.id,
            Identity.missing_since.is_(None),
        )
    ).one_or_none()
    if row is None:
        raise OverrideError("not_found")
    row.account_type_override = value
    session.add(
        AuditLog(
            tenant_id=tenant.id,
            at=now,
            actor=actor,
            action="identity.account_type",
            detail=f"identity_id={identity_id} art={value or 'automatisch'}",
        )
    )
    session.flush()
    with refresh_lock(tenant.id):
        refresh_identities(session, tenant, now)
    log.info("Kontoart gesetzt tenant_id=%s identity_id=%s", tenant.id, identity_id)
