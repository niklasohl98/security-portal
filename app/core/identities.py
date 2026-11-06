"""Identitäten eines Mandanten aus den jüngsten Läufen berechnen und in ``identity`` speichern.

Datenbasis ist je Quelle der jüngste Lauf mit Status ``success`` oder ``partial``; fehlgeschlagene
Läufe zählen nicht. Gibt es für den Mandanten noch gar keinen brauchbaren Lauf, bleibt die Tabelle
unverändert. Sonst entsteht pro zusammengeführtem Konto genau eine Zeile.

Zeilen ohne Konto im aktuellen Stand werden **nicht** gelöscht, sondern als verschwunden markiert
(``missing_since``, Status ``missing``, nicht geflaggt). Ob sie samt Ausnahmen gelöscht oder
behalten werden, entscheidet ein Mensch in der Prüfliste (``app/core/review.py``, ADR 0006). Taucht
das Konto wieder auf, verschwindet die Markierung samt Entscheidung.

``identity.key`` bleibt über Läufe stabil: die Entra-Objekt-ID, sonst die objectGUID. Taucht zu
einem bisher einzelnen AD-Konto später ein Entra-Partner auf (oder umgekehrt), wird die vorhandene
Zeile auf den neuen Schlüssel umgestellt, damit Ausnahmen und manuelle Kontoart erhalten bleiben.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Thresholds
from app.core.merge import Pair, merge_accounts
from app.core.rules import AccountType, Status, Subject, ad_disabled, evaluate, latest
from app.core.sync import WARN_NO_SIGN_IN, SyncError
from app.db.models import (
    AccountException,
    AdAccount,
    EntraAccount,
    Identity,
    RunStatus,
    SkuName,
    Source,
    SyncRun,
    Tenant,
    TenantSku,
    utcnow,
)

log = logging.getLogger(__name__)

_USABLE = (RunStatus.SUCCESS, RunStatus.PARTIAL)

# Eine Auswertung je Mandant gleichzeitig: zwei Läufe würden dieselben Zeilen anlegen wollen
# (``unique(tenant_id, key)``). Gilt im Prozess, wie die Sperren der Abrufe.
_refresh_locks: dict[int, threading.Lock] = {}
_refresh_guard = threading.Lock()


def refresh_lock(tenant_id: int) -> threading.Lock:
    """Sperre für die Auswertung eines Mandanten (im Prozess)."""
    with _refresh_guard:
        return _refresh_locks.setdefault(tenant_id, threading.Lock())


@dataclass(frozen=True)
class RefreshResult:
    """Kurzfassung für CLI und Oberfläche."""

    evaluated: bool  # False: keine brauchbaren Läufe, nichts geändert
    identities: int = 0
    flagged: int = 0
    ambiguous: int = 0  # Entra-Konten mit mehrdeutigem Zuordnungsschlüssel
    missing: int = 0  # verschwundene Identitäten, über die noch niemand entschieden hat
    matches: Mapping[str, int] = field(default_factory=dict)  # Paare je Zuordnungsstufe


def _latest_run(session: Session, tenant_id: int, source: Source) -> SyncRun | None:
    return session.scalars(
        select(SyncRun)
        .where(
            SyncRun.tenant_id == tenant_id,
            SyncRun.source == source,
            SyncRun.status.in_(_USABLE),
        )
        .order_by(SyncRun.id.desc())
        .limit(1)
    ).first()


def _free_sku_ids(session: Session, tenant_id: int, run_id: int | None) -> set[str]:
    """SKU-IDs des Entra-Laufs, die als kostenlos gelten (``sku_name.is_free``)."""
    if run_id is None:
        return set()
    free_parts = set(session.scalars(select(SkuName.part_number).where(SkuName.is_free)))
    rows = session.execute(
        select(TenantSku.sku_id, TenantSku.part_number).where(
            TenantSku.tenant_id == tenant_id, TenantSku.sync_run_id == run_id
        )
    )
    return {sku_id for sku_id, part in rows if part in free_parts}


def _is_licensed(entra: EntraAccount | None, free_sku_ids: set[str]) -> bool:
    if entra is None:
        return False
    return any(
        isinstance(lic, dict) and lic.get("sku_id") not in free_sku_ids for lic in entra.licenses
    )


def _local_part(upn: str | None) -> str:
    return upn.split("@", 1)[0] if upn else ""


def build_subject(
    pair: Pair,
    *,
    free_sku_ids: set[str],
    sign_in_known: bool,
    ad_expected: bool,
    has_exception: bool,
) -> Subject:
    """Fasst Entra- und AD-Konto eines Paares zu den Fakten für ``rules.evaluate`` zusammen."""
    e, a = pair.entra, pair.ad
    upn = (e.user_principal_name if e else None) or (a.user_principal_name if a else None)
    names = (
        _local_part(upn),
        (a.sam_account_name if a else None) or "",
        (e.on_prem_sam_account_name if e else None) or "",
    )
    created = [c for c in (e.created_at if e else None, a.when_created if a else None) if c]
    return Subject(
        names=names,
        in_entra=e is not None,
        in_ad=a is not None,
        is_guest=bool(
            e and ((e.user_type or "").casefold() == "guest" or "#ext#" in (upn or "").casefold())
        ),
        synced=bool(e and e.on_prem_sync_enabled),
        disabled=bool(
            (e and e.account_enabled is False) or (a and ad_disabled(a.user_account_control))
        ),
        created_at=min(created) if created else None,
        entra_last=latest((e.last_successful_sign_in, e.last_non_interactive_sign_in))
        if e
        else None,
        ad_last=latest((a.last_logon, a.last_logon_timestamp)) if a else None,
        licensed=_is_licensed(e, free_sku_ids),
        sign_in_known=sign_in_known,
        ad_expected=ad_expected,
        has_exception=has_exception,
    )


def _keys(pair: Pair) -> tuple[str, str | None]:
    """Primärer und (bei Paaren) zweiter Schlüssel der Identität."""
    if pair.entra is not None:
        return pair.entra.object_id, (pair.ad.object_guid if pair.ad else None)
    if pair.ad is None:
        raise ValueError("Paar ohne Konto")
    return pair.ad.object_guid, None


def _override(value: str | None) -> AccountType | None:
    try:
        return AccountType(value) if value else None
    except ValueError:
        return None


def _resolve_row(
    session: Session,
    existing: dict[str, Identity],
    excepted: set[int],
    tenant_id: int,
    pair: Pair,
) -> Identity:
    """Findet die vorhandene Zeile zum Paar (ggf. umgestellt) oder legt eine neue an.

    Gehen zwei Zeilen in einer auf, wandern die Ausnahmen mit; ``excepted`` (IDs mit gültiger
    Ausnahme) wird entsprechend nachgezogen.
    """
    primary, secondary = _keys(pair)
    row = existing.get(primary)
    other = existing.get(secondary) if secondary else None
    if row is not None and other is not None and other is not row:
        for ex in list(other.exceptions):
            ex.identity = row  # Ausnahmen der aufgegangenen Zeile mitnehmen
        if other.id in excepted:
            excepted.add(row.id)
        session.delete(other)
        session.flush()
        del existing[other.key]
    elif row is None and other is not None:
        del existing[other.key]
        other.key = primary
        row = other
    if row is None:
        row = Identity(tenant_id=tenant_id, key=primary)
        session.add(row)
    existing[primary] = row
    return row


def refresh_identities(
    session: Session, tenant: Tenant, now: datetime | None = None
) -> RefreshResult:
    """Berechnet alle Identitäten des Mandanten neu. Der Aufrufer übernimmt den Commit."""
    now = now or utcnow()
    entra_run = _latest_run(session, tenant.id, Source.ENTRA)
    ad_run = _latest_run(session, tenant.id, Source.AD)
    if entra_run is None and ad_run is None:
        return RefreshResult(evaluated=False)

    entra_rows = (
        list(
            session.scalars(
                select(EntraAccount)
                .where(
                    EntraAccount.tenant_id == tenant.id, EntraAccount.sync_run_id == entra_run.id
                )
                .order_by(EntraAccount.object_id)
            )
        )
        if entra_run
        else []
    )
    ad_rows = (
        list(
            session.scalars(
                select(AdAccount)
                .where(AdAccount.tenant_id == tenant.id, AdAccount.sync_run_id == ad_run.id)
                .order_by(AdAccount.object_guid)
            )
        )
        if ad_run
        else []
    )
    merged = merge_accounts(entra_rows, ad_rows, ad_available=ad_run is not None)

    free = _free_sku_ids(session, tenant.id, entra_run.id if entra_run else None)
    sign_in_known = entra_run is None or WARN_NO_SIGN_IN not in (entra_run.warnings or [])
    thresholds = Thresholds(
        inactive_days=tenant.inactive_days,
        long_inactive_days=tenant.long_inactive_days,
        new_account_grace_days=tenant.new_account_grace_days,
    )
    patterns: Mapping[str, list[str]] = tenant.account_patterns or {}
    existing = {
        i.key: i for i in session.scalars(select(Identity).where(Identity.tenant_id == tenant.id))
    }
    excepted = set(
        session.scalars(
            select(AccountException.identity_id).where(
                AccountException.tenant_id == tenant.id, AccountException.valid_until > now
            )
        )
    )

    seen: set[int] = set()
    flagged = 0
    for pair in merged.pairs:
        row = _resolve_row(session, existing, excepted, tenant.id, pair)
        session.flush()  # row.id für die Ausnahmeprüfung und stabile Schlüssel
        subject = build_subject(
            pair,
            free_sku_ids=free,
            sign_in_known=sign_in_known,
            ad_expected=ad_run is not None,
            has_exception=row.id in excepted,
        )
        verdict = evaluate(subject, thresholds, patterns, now, _override(row.account_type_override))
        row.entra_account_id = pair.entra.id if pair.entra else None
        row.ad_account_id = pair.ad.id if pair.ad else None
        row.display_name = (
            (pair.entra.display_name if pair.entra else None)
            or (pair.ad.sam_account_name if pair.ad else None)
            or (pair.entra.user_principal_name if pair.entra else None)
        )
        row.last_login_at = verdict.last_login
        row.account_type = verdict.account_type.value
        row.status = verdict.status.value
        row.flagged = verdict.flagged
        row.recommendation = verdict.recommendation
        row.missing_since = row.review_decision = row.reviewed_at = None
        flagged += verdict.flagged
        seen.add(row.id)

    pending_missing = 0
    for row in existing.values():
        if row.id in seen:
            continue
        if row.missing_since is None:
            row.missing_since = now
        row.status = Status.MISSING.value
        row.flagged = False
        row.recommendation = None
        pending_missing += row.review_decision is None
    session.flush()
    log.info(
        "Identitäten aktualisiert tenant_id=%s anzahl=%s geflaggt=%s mehrdeutig=%s verschwunden=%s",
        tenant.id,
        len(seen),
        flagged,
        merged.ambiguous,
        pending_missing,
    )
    matches = Counter(p.matched_by.value for p in merged.pairs if p.matched_by)
    return RefreshResult(True, len(seen), flagged, merged.ambiguous, pending_missing, dict(matches))


def run_identity_refresh(factory: sessionmaker[Session], slug: str) -> RefreshResult:
    """Berechnet die Identitäten eines Mandanten (nach Kürzel) und speichert sie."""
    with factory() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
        if tenant is None:
            raise SyncError("Mandant unbekannt")
        with refresh_lock(tenant.id):
            result = refresh_identities(session, tenant)
            session.commit()
        return result
