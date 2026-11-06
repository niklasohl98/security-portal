"""Hilfen für Tests mit Läufen, Konten und Identitäten. Alle Daten sind erfunden."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.identities import refresh_identities
from app.db.models import (
    AdAccount,
    EntraAccount,
    Identity,
    RunStatus,
    SkuName,
    Source,
    SyncRun,
    Tenant,
    TenantSku,
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


SKU_E3 = "00000000-0000-4000-8000-0000000000b1"
SKU_FREE = "00000000-0000-4000-8000-0000000000b2"
SKU_UNKNOWN = "00000000-0000-4000-8000-0000000000b3"


def seed_world(s: Session) -> None:
    """Mandant ``cg`` mit vier Konten, ausgewertet zum Zeitpunkt ``NOW``.

    ``Person 1``: Entra, 200 Tage inaktiv, E3 und eine kostenlose Lizenz. ``Person 2``: Entra, nie
    angemeldet, unbekannte SKU. ``Person 3``: Entra, aktiv. ``nur.ad``: nur AD, 50 Tage.
    """
    t = get_tenant(s)
    run = make_run(s, t, Source.ENTRA)
    ad_run = make_run(s, t, Source.AD)
    s.add_all(
        [
            SkuName(part_number="SPE_E3", display_name="Microsoft 365 E3"),
            SkuName(part_number="FLOW_FREE", display_name="Flow Free", is_free=True),
            TenantSku(tenant_id=t.id, sync_run_id=run.id, sku_id=SKU_E3, part_number="SPE_E3"),
            TenantSku(tenant_id=t.id, sync_run_id=run.id, sku_id=SKU_FREE, part_number="FLOW_FREE"),
            TenantSku(tenant_id=t.id, sync_run_id=run.id, sku_id=SKU_UNKNOWN, part_number="NEU_X"),
        ]
    )
    make_entra(s, run, 1, last_successful_sign_in=ago(200), licenses=[lic(SKU_FREE), lic(SKU_E3)])
    make_entra(s, run, 2, licenses=[lic(SKU_UNKNOWN)])
    make_entra(s, run, 3, last_successful_sign_in=ago(2))
    make_ad(
        s,
        ad_run,
        4,
        sam_account_name="nur.ad",
        last_logon=ago(50),
        last_logon_per_dc={"dc1.beispiel.test": ago(50).isoformat(), "dc2.beispiel.test": None},
        ou="OU=Test,DC=beispiel,DC=test",
        user_account_control=0x202,
    )
    refresh_identities(s, t, NOW)
