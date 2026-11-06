"""Hilfen für Tests mit Läufen, Konten und Identitäten. Alle Daten sind erfunden."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AdAccount,
    EntraAccount,
    Identity,
    RunStatus,
    Source,
    SyncRun,
    Tenant,
)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def ago(days: float) -> datetime:
    """Zeitpunkt vor ``days`` Tagen, gemessen an ``NOW``."""
    return NOW - timedelta(days=days)


def get_tenant(s: Session, slug: str = "cg") -> Tenant:
    return s.scalars(select(Tenant).where(Tenant.slug == slug)).one()


def make_run(
    s: Session,
    tenant: Tenant,
    source: Source,
    status: RunStatus = RunStatus.SUCCESS,
    warnings: list[str] | None = None,
) -> SyncRun:
    run = SyncRun(
        tenant_id=tenant.id,
        source=source,
        status=status,
        started_at=NOW,
        warnings=warnings or [],
    )
    s.add(run)
    s.flush()
    return run


def make_entra(s: Session, run: SyncRun, n: int, **kw: Any) -> EntraAccount:
    kw.setdefault("user_principal_name", f"user{n}@beispiel.test")
    kw.setdefault("display_name", f"Person {n}")
    kw.setdefault("created_at", ago(400))
    acc = EntraAccount(
        tenant_id=run.tenant_id, sync_run_id=run.id, object_id=f"e{n:07d}-0000-4000-8000-0", **kw
    )
    s.add(acc)
    s.flush()
    return acc


def make_ad(s: Session, run: SyncRun, n: int, **kw: Any) -> AdAccount:
    kw.setdefault("object_guid", f"{n:08d}-0000-4000-8000-0")
    acc = AdAccount(tenant_id=run.tenant_id, sync_run_id=run.id, **kw)
    s.add(acc)
    s.flush()
    return acc


def lic(sku: str) -> dict[str, Any]:
    """Ein Lizenzeintrag, wie ihn der Entra-Abruf speichert."""
    return {"sku_id": sku, "direct": True, "group": False, "state": "Active"}


def by_name(s: Session, tenant: Tenant) -> dict[str, Identity]:
    """Identitäten des Mandanten nach Anzeigename."""
    rows = s.scalars(select(Identity).where(Identity.tenant_id == tenant.id))
    return {i.display_name or "": i for i in rows}
