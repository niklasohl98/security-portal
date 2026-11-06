"""Bereinigung der Rohdaten: Konten und Lizenzbestände alter Läufe nach ``retention_days`` löschen.

Rohdaten bleiben so lange, damit sich Änderungen zwischen Läufen nachvollziehen lassen. Immer
erhalten bleibt der jüngste brauchbare Lauf (``success``/``partial``) je Mandant und Quelle: Die
Auswertung liest genau diese Daten. Die Läufe selbst (``sync_run``) bleiben als Protokoll; sie
enthalten keine Kontoinhalte. Verweise aus ``identity`` auf gelöschte Konten werden zu ``NULL``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AdAccount,
    EntraAccount,
    RunStatus,
    SyncRun,
    TenantSku,
    utcnow,
)

log = logging.getLogger(__name__)

_USABLE = (RunStatus.SUCCESS, RunStatus.PARTIAL)


@dataclass(frozen=True)
class PurgeResult:
    """Anzahl gelöschter Zeilen."""

    runs: int  # betroffene Läufe
    entra_accounts: int
    ad_accounts: int
    skus: int


def _protected_run_ids(session: Session) -> set[int]:
    """Jüngster brauchbarer Lauf je (Mandant, Quelle)."""
    newest = (
        select(func.max(SyncRun.id))
        .where(SyncRun.status.in_(_USABLE))
        .group_by(SyncRun.tenant_id, SyncRun.source)
    )
    return set(session.scalars(newest))


def purge_raw_data(
    session: Session, retention_days: int, now: datetime | None = None
) -> PurgeResult:
    """Löscht Rohdaten von Läufen, die älter als ``retention_days`` sind. Commit macht der Aufrufer.

    Raises:
        ValueError: bei ``retention_days < 1`` (sonst würde alles Aktuelle gelöscht).
    """
    if retention_days < 1:
        raise ValueError("retention_days muss mindestens 1 sein")
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    protected = _protected_run_ids(session)
    old = select(SyncRun.id).where(SyncRun.started_at < cutoff)
    if protected:
        old = old.where(SyncRun.id.not_in(protected))
    run_ids = list(session.scalars(old))
    if not run_ids:
        return PurgeResult(0, 0, 0, 0)
    counts = []
    for model in (EntraAccount, AdAccount, TenantSku):
        result = cast(
            "CursorResult[Any]",
            session.execute(delete(model).where(model.sync_run_id.in_(run_ids))),
        )
        counts.append(result.rowcount)
    log.info(
        "Rohdaten bereinigt laeufe=%s entra=%s ad=%s skus=%s aelter_als_tage=%s",
        len(run_ids),
        counts[0],
        counts[1],
        counts[2],
        retention_days,
    )
    return PurgeResult(len(run_ids), counts[0], counts[1], counts[2])
