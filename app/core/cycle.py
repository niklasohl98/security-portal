"""Abrufzyklus: Entra, AD und Auswertung je Mandant; nächtlicher Lauf mit Bereinigung.

Ein Zyklus je Mandant ruft nacheinander die konfigurierten Quellen ab (Entra, dann AD) und
berechnet danach die Identitäten neu, auch wenn eine Quelle scheiterte (die Auswertung nimmt je
Quelle den jüngsten brauchbaren Lauf). Fehler eines Mandanten stoppen die anderen nie. Doppelläufe
verhindern die Sperren je (Mandant, Quelle) in ``sync``/``ad_sync`` und eine Sperre je Zyklus; ein
belegter Zyklus wird übersprungen, nie gestapelt. Logs enthalten nur Zähler und technische IDs.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.ad_sync import run_ad_sync
from app.core.identities import RefreshResult, run_identity_refresh
from app.core.retention import PurgeResult, purge_raw_data
from app.core.sync import SyncBusyError, SyncError, run_entra_sync
from app.db.models import Source, Tenant

log = logging.getLogger(__name__)

_cycle_locks: dict[str, threading.Lock] = {}
_cycle_guard = threading.Lock()
_ALL = "*"  # Schlüssel der Sperre für „alle Mandanten“


@dataclass(frozen=True)
class SourceOutcome:
    """Ergebnis eines Abrufs; ``status`` ist ein Laufstatus oder ``busy``/``error``/``skipped``."""

    source: Source
    status: str
    run_id: int | None = None


@dataclass
class CycleResult:
    """Ergebnis des Zyklus eines Mandanten."""

    slug: str
    outcomes: list[SourceOutcome] = field(default_factory=list)
    refresh: RefreshResult | None = None
    skipped: bool = False  # Zyklus lief schon


def _lock(key: str) -> threading.Lock:
    with _cycle_guard:
        return _cycle_locks.setdefault(key, threading.Lock())


def _sources(tenant: Tenant) -> list[Source]:
    found = []
    if tenant.entra_tenant_id:
        found.append(Source.ENTRA)
    if tenant.ad_domain:
        found.append(Source.AD)
    return found


def _fetch(
    factory: sessionmaker[Session], settings: Settings, slug: str, source: Source
) -> SourceOutcome:
    try:
        if source is Source.ENTRA:
            result = run_entra_sync(factory, slug, settings.secrets_dir)
        else:
            result = run_ad_sync(factory, slug, settings.secrets_dir, settings.tenants_file)
    except SyncBusyError:
        return SourceOutcome(source, "busy")
    except SyncError:
        return SourceOutcome(source, "skipped")
    except Exception:
        log.exception("Abruf mit unerwartetem Fehler quelle=%s", source.value)
        return SourceOutcome(source, "error")
    return SourceOutcome(source, result.status.value, result.run_id)


def run_tenant_cycle(factory: sessionmaker[Session], settings: Settings, slug: str) -> CycleResult:
    """Ruft die Quellen eines Mandanten ab und wertet danach aus.

    Läuft für diesen Mandanten schon ein Zyklus, wird nichts getan (``skipped=True``).
    """
    result = CycleResult(slug)
    lock = _lock(slug)
    if not lock.acquire(blocking=False):
        result.skipped = True
        return result
    try:
        with factory() as session:
            tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
            if tenant is None or not tenant.enabled:
                result.skipped = True
                return result
            sources = _sources(tenant)
        for source in sources:
            result.outcomes.append(_fetch(factory, settings, slug, source))
        try:
            result.refresh = run_identity_refresh(factory, slug)
        except Exception:
            log.exception("Auswertung mit unerwartetem Fehler")
    finally:
        lock.release()
    log.info(
        "Zyklus beendet quellen=%s ausgewertet=%s",
        ",".join(f"{o.source.value}:{o.status}" for o in result.outcomes) or "-",
        result.refresh.evaluated if result.refresh else False,
    )
    return result


def run_all(factory: sessionmaker[Session], settings: Settings) -> list[CycleResult]:
    """Zyklus für alle aktiven Mandanten nacheinander. Läuft schon einer, kommt ``[]``."""
    lock = _lock(_ALL)
    if not lock.acquire(blocking=False):
        log.warning("Zyklus für alle Mandanten läuft bereits, übersprungen")
        return []
    try:
        with factory() as session:
            slugs = list(
                session.scalars(
                    select(Tenant.slug).where(Tenant.enabled.is_(True)).order_by(Tenant.slug)
                )
            )
        return [run_tenant_cycle(factory, settings, slug) for slug in slugs]
    finally:
        lock.release()


def run_nightly(factory: sessionmaker[Session], settings: Settings) -> PurgeResult | None:
    """Täglicher Lauf: alle Mandanten, danach Bereinigung der Rohdaten.

    Gibt ``None`` zurück, wenn der Zyklus schon lief; Fehler der Bereinigung werden geloggt.
    """
    if not run_all(factory, settings):
        return None
    try:
        with factory.begin() as session:
            return purge_raw_data(session, settings.retention_days)
    except Exception:
        log.exception("Bereinigung mit unerwartetem Fehler")
        return None
