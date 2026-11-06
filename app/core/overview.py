"""Kennzahlen je Mandant für die Übersicht und das Laufprotokoll.

Liest nur die Datenbank, filtert jede Abfrage nach ``tenant_id``. Freiwerdende Lizenzen zählen
die nicht kostenlosen Lizenzen geflaggter Konten je SKU (Ausnahmen sind nie geflaggt).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.accounts import AccountRow
from app.db.models import (
    AccountException,
    Identity,
    RunStatus,
    Source,
    SyncRun,
    Tenant,
)


@dataclass(frozen=True)
class RunInfo:
    """Ein Lauf für Übersicht und Protokoll (nur technische Angaben)."""

    run_id: int
    tenant_slug: str
    tenant_name: str
    source: Source
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    account_count: int
    dcs_checked: int
    dcs_defective: list[dict[str, str | None]]
    warnings: list[str]
    error: str | None

    @property
    def duration_seconds(self) -> int | None:
        """Dauer in Sekunden; ``None`` solange der Lauf nicht beendet ist."""
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds())


@dataclass
class TenantOverview:
    """Kennzahlen eines Mandanten."""

    slug: str
    name: str
    total: int = 0
    flagged: int = 0
    flagged_licensed: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    releasing_licenses: dict[str, int] = field(default_factory=dict)  # Anzeigename -> Anzahl
    latest_runs: list[RunInfo] = field(default_factory=list)  # je Quelle der jüngste Lauf
    warnings: list[str] = field(default_factory=list)  # feste Codes, ohne Doppelte
    missing_open: int = 0  # verschwundene Identitäten ohne Entscheidung
    exceptions_expiring: int = 0  # gültig, laufen innerhalb der Vorwarnzeit ab
    exceptions_expired: int = 0


def _info(run: SyncRun, tenant: Tenant) -> RunInfo:
    return RunInfo(
        run_id=run.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        source=run.source,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        account_count=run.account_count,
        dcs_checked=run.dcs_checked,
        dcs_defective=[d for d in (run.dcs_defective or []) if isinstance(d, dict)],
        warnings=[str(w) for w in (run.warnings or [])],
        error=run.error,
    )


def latest_runs(session: Session, tenant: Tenant) -> list[RunInfo]:
    """Je Quelle der jüngste Lauf (egal mit welchem Status), Entra vor AD."""
    runs = []
    for source in (Source.ENTRA, Source.AD):
        run = session.scalars(
            select(SyncRun)
            .where(SyncRun.tenant_id == tenant.id, SyncRun.source == source)
            .order_by(SyncRun.id.desc())
            .limit(1)
        ).first()
        if run is not None:
            runs.append(_info(run, tenant))
    return runs


def list_runs(session: Session, tenants: list[Tenant], limit: int = 100) -> list[RunInfo]:
    """Die jüngsten Läufe aller angegebenen Mandanten, neueste zuerst."""
    by_id = {t.id: t for t in tenants}
    if not by_id:
        return []
    runs = session.scalars(
        select(SyncRun)
        .where(SyncRun.tenant_id.in_(by_id))
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(limit)
    ).all()
    return [_info(r, by_id[r.tenant_id]) for r in runs]


def tenant_overview(
    session: Session, tenant: Tenant, rows: list[AccountRow], now: datetime
) -> TenantOverview:
    """Kennzahlen aus den geladenen Konten (``accounts.load_accounts``) und der Datenbank."""
    out = TenantOverview(slug=tenant.slug, name=tenant.name, total=len(rows))
    out.by_status = dict(Counter(r.status or "-" for r in rows))
    releasing: Counter[str] = Counter()
    for r in rows:
        if not r.flagged:
            continue
        out.flagged += 1
        if r.licensed:
            out.flagged_licensed += 1
            releasing.update(lic.name for lic in r.paid_licenses)
    out.releasing_licenses = dict(sorted(releasing.items(), key=lambda kv: (-kv[1], kv[0])))

    out.latest_runs = latest_runs(session, tenant)
    for run in out.latest_runs:
        for code in run.warnings:
            if code not in out.warnings:
                out.warnings.append(code)
        if run.status is RunStatus.FAILED and "run_failed" not in out.warnings:
            out.warnings.append("run_failed")

    out.missing_open = (
        session.scalar(
            select(func.count()).where(
                Identity.tenant_id == tenant.id,
                Identity.missing_since.is_not(None),
                Identity.review_decision.is_(None),
            )
        )
        or 0
    )
    warn_until = now + timedelta(days=tenant.exception_warn_days)
    valid = AccountException.valid_until
    out.exceptions_expiring = (
        session.scalar(
            select(func.count()).where(
                AccountException.tenant_id == tenant.id, valid > now, valid <= warn_until
            )
        )
        or 0
    )
    out.exceptions_expired = (
        session.scalar(
            select(func.count()).where(AccountException.tenant_id == tenant.id, valid <= now)
        )
        or 0
    )
    return out
