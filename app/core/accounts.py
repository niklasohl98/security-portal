"""Kontenansicht: Identitäten eines Mandanten mit allen Anzeigewerten laden, filtern, sortieren.

Liest nur die Datenbank. Jede Abfrage filtert nach ``tenant_id``. Verschwundene Identitäten
(``missing_since``) gehören in die Prüfliste und erscheinen hier nicht. Dieselbe Ansicht speist
Oberfläche und Export (der Export enthält immer die aktuelle Filteransicht).

Gefiltert wird im Speicher: ein Mandant hat einige hundert Konten, und „lizenziert“ ergibt sich erst
aus den Lizenzen des Entra-Kontos und der Tabelle ``sku_name``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AccountException,
    AdAccount,
    EntraAccount,
    Identity,
    SkuName,
    Tenant,
    TenantSku,
)

SOURCES = ("entra", "ad", "both")


@dataclass(frozen=True)
class LicenseView:
    """Eine Lizenz eines Kontos für die Anzeige."""

    sku_id: str
    name: str
    free: bool
    direct: bool
    group: bool
    state: str


@dataclass(frozen=True)
class AccountRow:
    """Ein Konto (Identität) mit allen Werten für Liste, Detail und Export."""

    identity_id: int
    tenant_slug: str
    tenant_name: str
    display_name: str | None
    upn: str | None
    sam_account_name: str | None
    mail: str | None
    status: str | None
    account_type: str | None
    account_type_manual: bool
    flagged: bool
    recommendation: str | None
    last_login_at: datetime | None
    days_since_login: int | None
    source: str  # "entra", "ad" oder "both"
    licenses: list[LicenseView] = field(default_factory=list)
    exception_until: datetime | None = None
    entra: EntraAccount | None = None
    ad: AdAccount | None = None

    @property
    def paid_licenses(self) -> list[LicenseView]:
        """Lizenzen, die nicht als kostenlos gelten."""
        return [lic for lic in self.licenses if not lic.free]

    @property
    def licensed(self) -> bool:
        """Mindestens eine nicht kostenlose Lizenz."""
        return bool(self.paid_licenses)

    @property
    def enabled(self) -> bool | None:
        """Entra ``accountEnabled``; ``None`` bei reinen AD-Konten oder fehlender Angabe."""
        return self.entra.account_enabled if self.entra else None


@dataclass(frozen=True)
class AccountFilter:
    """Filter der Kontenliste. Leere Felder filtern nicht."""

    flagged_only: bool = True
    status: str | None = None
    account_type: str | None = None
    licensed: bool | None = None
    source: str | None = None
    query: str | None = None


def _sku_table(session: Session, run_ids: set[int]) -> dict[tuple[int, str], tuple[str, bool]]:
    """(Lauf, SKU-ID) -> (Anzeigename, kostenlos); unbekannte Namen fallen auf die Teilenummer."""
    if not run_ids:
        return {}
    names = {n.part_number: (n.display_name, n.is_free) for n in session.scalars(select(SkuName))}
    rows = session.execute(
        select(TenantSku.sync_run_id, TenantSku.sku_id, TenantSku.part_number).where(
            TenantSku.sync_run_id.in_(run_ids)
        )
    )
    return {(run_id, sku_id): names.get(part, (part, False)) for run_id, sku_id, part in rows}


def _licenses(
    entra: EntraAccount | None, skus: dict[tuple[int, str], tuple[str, bool]]
) -> list[LicenseView]:
    if entra is None:
        return []
    found = []
    for lic in entra.licenses:
        if not isinstance(lic, dict):
            continue
        sku_id = str(lic.get("sku_id", ""))
        name, free = skus.get((entra.sync_run_id, sku_id), (sku_id, False))
        found.append(
            LicenseView(
                sku_id=sku_id,
                name=name,
                free=free,
                direct=bool(lic.get("direct")),
                group=bool(lic.get("group")),
                state=str(lic.get("state", "Active")),
            )
        )
    return sorted(found, key=lambda x: (x.free, x.name.casefold()))


def _row(
    tenant: Tenant,
    identity: Identity,
    entra: EntraAccount | None,
    ad: AdAccount | None,
    skus: dict[tuple[int, str], tuple[str, bool]],
    exceptions: dict[int, datetime],
    now: datetime,
) -> AccountRow:
    last = identity.last_login_at
    return AccountRow(
        identity_id=identity.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        display_name=identity.display_name,
        upn=(entra.user_principal_name if entra else None)
        or (ad.user_principal_name if ad else None),
        sam_account_name=(ad.sam_account_name if ad else None)
        or (entra.on_prem_sam_account_name if entra else None),
        mail=(entra.mail if entra else None) or (ad.mail if ad else None),
        status=identity.status,
        account_type=identity.account_type_override or identity.account_type,
        account_type_manual=identity.account_type_override is not None,
        flagged=identity.flagged,
        recommendation=identity.recommendation,
        last_login_at=last,
        days_since_login=(now - last).days if last else None,
        source="both" if entra and ad else ("entra" if entra else "ad"),
        licenses=_licenses(entra, skus),
        exception_until=exceptions.get(identity.id),
        entra=entra,
        ad=ad,
    )


def load_accounts(session: Session, tenant: Tenant, now: datetime) -> list[AccountRow]:
    """Alle nicht verschwundenen Identitäten des Mandanten mit Konten, Lizenzen und Ausnahme."""
    rows = session.execute(
        select(Identity, EntraAccount, AdAccount)
        .outerjoin(
            EntraAccount,
            (EntraAccount.id == Identity.entra_account_id) & (EntraAccount.tenant_id == tenant.id),
        )
        .outerjoin(
            AdAccount,
            (AdAccount.id == Identity.ad_account_id) & (AdAccount.tenant_id == tenant.id),
        )
        .where(Identity.tenant_id == tenant.id, Identity.missing_since.is_(None))
    ).all()
    skus = _sku_table(session, {e.sync_run_id for _i, e, _a in rows if e is not None})
    exceptions: dict[int, datetime] = dict(
        session.execute(
            select(AccountException.identity_id, func.max(AccountException.valid_until))
            .where(AccountException.tenant_id == tenant.id, AccountException.valid_until > now)
            .group_by(AccountException.identity_id)
        ).all()
    )
    return [_row(tenant, i, e, a, skus, exceptions, now) for i, e, a in rows]


def get_account(
    session: Session, tenant: Tenant, identity_id: int, now: datetime
) -> AccountRow | None:
    """Ein Konto des Mandanten oder ``None`` (andere Mandanten und Verschwundene zählen nicht)."""
    return next(
        (r for r in load_accounts(session, tenant, now) if r.identity_id == identity_id), None
    )


def _matches_text(row: AccountRow, needle: str) -> bool:
    fields: tuple[Any, ...] = (row.display_name, row.upn, row.sam_account_name, row.mail)
    return any(needle in str(f).casefold() for f in fields if f)


def apply_filter(rows: list[AccountRow], flt: AccountFilter) -> list[AccountRow]:
    """Wendet den Filter an und sortiert nach Tagen seit Anmeldung (nie angemeldet zuerst)."""
    needle = (flt.query or "").strip().casefold()
    kept = [
        r
        for r in rows
        if (not flt.flagged_only or r.flagged)
        and (flt.status is None or r.status == flt.status)
        and (flt.account_type is None or r.account_type == flt.account_type)
        and (flt.licensed is None or r.licensed is flt.licensed)
        and (flt.source is None or r.source == flt.source)
        and (not needle or _matches_text(r, needle))
    ]
    return sorted(
        kept,
        key=lambda r: (
            r.days_since_login is not None,
            -(r.days_since_login or 0),
            (r.display_name or "").casefold(),
            r.identity_id,
        ),
    )
