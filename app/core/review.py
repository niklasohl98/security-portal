"""Prüfliste verschwundener Identitäten: behalten, löschen oder wieder zur Prüfung stellen.

Eine Identität gilt als verschwunden, wenn ``refresh_identities`` für sie in den jüngsten Läufen
kein Konto mehr fand (``missing_since``). Sie wird nie automatisch gelöscht (ADR 0006): Ein
unvollständiger Lauf (``partial``) oder ein DC-Ausfall könnte sonst Ausnahmen vernichten.

Entscheidungen betreffen nur die Tabelle ``identity`` (samt Ausnahmen) dieses Tools. Entra und AD
werden nie angefasst. Jede Entscheidung steht im Audit-Log, ohne Namen oder Kontoinhalte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AccountException, AuditLog, Identity, Tenant, utcnow

log = logging.getLogger(__name__)

KEEP = "keep"  # Wert in ``identity.review_decision``


class Decision(StrEnum):
    """Mögliche Entscheidungen der Prüfliste."""

    KEEP = "keep"
    DELETE = "delete"
    REOPEN = "reopen"


class ReviewError(Exception):
    """Entscheidung nicht möglich. ``code`` ist ein fester Text ohne Kontoinhalte."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MissingItem:
    """Eine verschwundene Identität für die Anzeige."""

    identity_id: int
    display_name: str | None
    key: str
    account_type: str | None
    last_login_at: datetime | None
    missing_since: datetime
    exception_count: int
    decided: bool  # True = „behalten“ entschieden


def _count_exceptions(session: Session, tenant: Tenant) -> dict[int, int]:
    return dict(
        session.execute(
            select(AccountException.identity_id, func.count())
            .where(AccountException.tenant_id == tenant.id)
            .group_by(AccountException.identity_id)
        ).all()
    )


def _item(row: Identity, missing_since: datetime, exception_count: int) -> MissingItem:
    return MissingItem(
        identity_id=row.id,
        display_name=row.display_name,
        key=row.key,
        account_type=row.account_type,
        last_login_at=row.last_login_at,
        missing_since=missing_since,
        exception_count=exception_count,
        decided=row.review_decision is not None,
    )


def list_missing(session: Session, tenant: Tenant, *, decided: bool = False) -> list[MissingItem]:
    """Verschwundene Identitäten des Mandanten, älteste zuerst.

    Args:
        decided: ``False`` = noch offene, ``True`` = bereits mit „behalten“ entschiedene.
    """
    cond = Identity.review_decision.is_not(None) if decided else Identity.review_decision.is_(None)
    rows = session.scalars(
        select(Identity)
        .where(Identity.tenant_id == tenant.id, Identity.missing_since.is_not(None), cond)
        .order_by(Identity.missing_since, Identity.id)
    ).all()
    counts = _count_exceptions(session, tenant)
    return [
        _item(r, r.missing_since, counts.get(r.id, 0)) for r in rows if r.missing_since is not None
    ]


def get_missing(session: Session, tenant: Tenant, identity_id: int) -> MissingItem | None:
    """Eine verschwundene Identität (offen oder entschieden) oder ``None``."""
    row = session.scalars(
        select(Identity).where(
            Identity.id == identity_id,
            Identity.tenant_id == tenant.id,
            Identity.missing_since.is_not(None),
        )
    ).one_or_none()
    if row is None or row.missing_since is None:
        return None
    counts = _count_exceptions(session, tenant)
    return _item(row, row.missing_since, counts.get(row.id, 0))


def _audit(
    session: Session, tenant: Tenant, actor: str, decision: Decision, row: Identity, at: datetime
) -> None:
    count = session.scalar(
        select(func.count()).where(
            AccountException.tenant_id == tenant.id, AccountException.identity_id == row.id
        )
    )
    session.add(
        AuditLog(
            tenant_id=tenant.id,
            at=at,
            actor=actor,
            action=f"identity.{decision.value}",
            detail=f"identity_id={row.id} ausnahmen={count}",
        )
    )


def decide(
    session: Session,
    tenant: Tenant,
    identity_id: int,
    decision: Decision,
    actor: str,
    now: datetime | None = None,
) -> None:
    """Setzt eine Entscheidung um. Der Aufrufer übernimmt den Commit.

    - ``KEEP``: nur für offene verschwundene Identitäten; sie bleibt samt Ausnahmen bestehen.
    - ``DELETE``: für offene und behaltene; löscht die Zeile samt Ausnahmen (nicht rückgängig).
    - ``REOPEN``: nur für behaltene; stellt sie wieder zur Prüfung.

    Raises:
        ReviewError: ``not_found`` (gibt es bei diesem Mandanten nicht), ``not_missing`` (nicht
            verschwunden), ``bad_state`` (Entscheidung passt nicht zum Zustand).
    """
    now = now or utcnow()
    row = session.scalars(
        select(Identity).where(Identity.id == identity_id, Identity.tenant_id == tenant.id)
    ).one_or_none()
    if row is None:
        raise ReviewError("not_found")
    if row.missing_since is None:
        raise ReviewError("not_missing")
    kept = row.review_decision == KEEP
    if (decision is Decision.KEEP and kept) or (decision is Decision.REOPEN and not kept):
        raise ReviewError("bad_state")

    _audit(session, tenant, actor, decision, row, now)
    if decision is Decision.DELETE:
        session.delete(row)
    elif decision is Decision.KEEP:
        row.review_decision, row.reviewed_at = KEEP, now
    else:
        row.review_decision = row.reviewed_at = None
    session.flush()
    log.info(
        "Prüfentscheidung tenant_id=%s identity_id=%s entscheidung=%s",
        tenant.id,
        identity_id,
        decision.value,
    )
