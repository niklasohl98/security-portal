"""AD-Abruf je Mandant: Lauf anlegen, DCs abfragen, speichern, Status setzen.

Gleiches Muster wie ``app/core/sync.py`` (Entra): eine Sperre je Mandant und Quelle, Fehler landen
als ``failed`` im Lauf und werden nicht weitergeworfen, Fehlertexte enthalten nur feste Codes.
Der Abruf ist rein lesend.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, read_secret, resolve_ca_file
from app.connectors.ad import (
    WARN_DC_DEFECTIVE,
    WARN_DC_SUSPECT,
    WARN_DC_UNREACHABLE,
    WARN_MASTER_INCOMPLETE,
    AdCollection,
    DcReport,
    DcStatus,
    NoHealthyDcError,
    collect_ad,
    discover_dcs,
)
from app.connectors.ad_client import AdError, Directory, Opener, TlsPolicy, bind_name, open_ldaps
from app.connectors.ad_parse import sam_from_bind_user
from app.core.sync import SyncBusyError, SyncError, SyncResult, _lock_for
from app.db.models import AdAccount, RunStatus, Source, SyncRun, Tenant, utcnow

log = logging.getLogger(__name__)

WARN_WEAK_KEY_ACTIVE = "ad_weak_key_exception_active"
WARN_WEAK_KEY_EXPIRED = "ad_weak_key_exception_expired"

# Diese Warnungen setzen den Lauf auf „partial“ (Daten evtl. unvollständig); die beiden
# Ausnahme-Hinweise sind nur Information.
_PARTIAL_WARNINGS = {
    WARN_DC_UNREACHABLE,
    WARN_DC_DEFECTIVE,
    WARN_DC_SUSPECT,
    WARN_MASTER_INCOMPLETE,
}


@dataclass(frozen=True)
class AdSyncResult(SyncResult):
    """Ergebnis eines AD-Laufs; zusätzlich die Prüfergebnisse je DC."""

    dcs: list[DcReport] = field(default_factory=list)


@dataclass(frozen=True)
class AdRuntime:
    """Aus Mandant und Secrets abgeleitete Verbindungsangaben."""

    domain: str
    configured_dcs: list[str]
    service_account: str
    opener: Opener
    notices: list[str]


def build_ad_runtime(
    tenant: Tenant, secrets_dir: Path, tenants_file: Path, today: date | None = None
) -> AdRuntime:
    """Baut Opener und Hinweise für einen Mandanten (Passwort aus dem Docker-Secret).

    Raises:
        SyncError: AD ist für den Mandanten nicht konfiguriert.
        ConfigError: Secret oder CA-Datei nicht lesbar.
    """
    if not (tenant.ad_domain and tenant.ad_bind_user and tenant.ad_bind_secret):
        raise SyncError("AD ist für diesen Mandanten nicht konfiguriert")
    password = read_secret(tenant.ad_bind_secret, secrets_dir)
    ca = resolve_ca_file(tenants_file, tenant.ad_ca_file) if tenant.ad_ca_file else None
    notices: list[str] = []
    allow_weak = False
    if tenant.ad_weak_keys_until is not None:
        if (today or datetime.now(UTC).date()) <= tenant.ad_weak_keys_until:
            allow_weak = True
            notices.append(WARN_WEAK_KEY_ACTIVE)
        else:
            notices.append(WARN_WEAK_KEY_EXPIRED)
    policy = TlsPolicy(ca_file=ca, allow_weak_keys=allow_weak)
    login = bind_name(tenant.ad_bind_user, tenant.ad_domain)

    def opener(host: str) -> Directory:
        return open_ldaps(host, bind_user=login, password=password, policy=policy)

    return AdRuntime(
        domain=tenant.ad_domain,
        configured_dcs=[str(h) for h in tenant.ad_dcs],
        service_account=sam_from_bind_user(tenant.ad_bind_user),
        opener=opener,
        notices=notices,
    )


def run_ad_sync(
    factory: sessionmaker[Session],
    slug: str,
    secrets_dir: Path,
    tenants_file: Path,
    *,
    opener: Opener | None = None,
    resolver: Callable[[str], list[str]] | None = None,
) -> AdSyncResult:
    """Ruft die Benutzer eines Mandanten aus dem AD ab und speichert sie.

    Args:
        factory: Sitzungsfabrik.
        slug: Kürzel des Mandanten.
        secrets_dir: Verzeichnis der Docker-Secrets.
        tenants_file: Pfad der ``tenants.yaml`` (Wurzel für ``ca_file``).
        opener: Verbindungsöffner (Tests); Standard ist LDAPS aus ``build_ad_runtime``.
        resolver: DNS-SRV-Suche (Tests); Standard ist ``resolve_srv``.

    Returns:
        Ergebnis des Laufs. ``failed`` bei Fehlern im Abruf (nicht als Exception).

    Raises:
        SyncError: Mandant unbekannt, deaktiviert oder ohne AD-Konfiguration.
        SyncBusyError: Es läuft schon ein AD-Abruf für diesen Mandanten.
    """
    with factory() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
        if tenant is None or not tenant.enabled:
            raise SyncError("Mandant unbekannt oder deaktiviert")
        if tenant.ad_domain is None:
            raise SyncError("AD ist für diesen Mandanten nicht konfiguriert")
        tenant_id = tenant.id

    lock = _lock_for(tenant_id, Source.AD)
    if not lock.acquire(blocking=False):
        raise SyncBusyError("Abruf läuft bereits")
    try:
        return _run_locked(factory, tenant_id, slug, secrets_dir, tenants_file, opener, resolver)
    finally:
        lock.release()


def _run_locked(
    factory: sessionmaker[Session],
    tenant_id: int,
    slug: str,
    secrets_dir: Path,
    tenants_file: Path,
    opener: Opener | None,
    resolver: Callable[[str], list[str]] | None,
) -> AdSyncResult:
    started = time.monotonic()
    with factory.begin() as session:
        run = SyncRun(tenant_id=tenant_id, source=Source.AD, status=RunStatus.RUNNING)
        session.add(run)
        session.flush()
        run_id = run.id
    log.info("AD-Abruf gestartet: mandant=%s lauf=%d", slug, run_id)

    notices: list[str] = []
    try:
        with factory() as session:
            tenant = session.get(Tenant, tenant_id)
            assert tenant is not None  # noqa: S101 - oben geprüft
            runtime = build_ad_runtime(tenant, secrets_dir, tenants_file)
        notices = runtime.notices
        hosts = discover_dcs(
            runtime.domain, runtime.configured_dcs, *([resolver] if resolver else [])
        )
        collection = collect_ad(hosts, opener or runtime.opener, runtime.service_account)
    except NoHealthyDcError as exc:
        return _finish_failed(factory, run_id, slug, exc, exc.reports, notices)
    except (AdError, ConfigError, SyncError) as exc:
        return _finish_failed(factory, run_id, slug, exc, [], notices)
    except Exception as exc:  # noqa: BLE001 - ein unerwarteter Fehler darf den Zeitplan nicht beenden
        return _finish_failed(factory, run_id, slug, exc, [], notices)

    result = _store_results(factory, tenant_id, run_id, collection, notices)
    log.info(
        "AD-Abruf fertig: mandant=%s lauf=%d konten=%d dcs=%d warnungen=%d dauer=%.1fs",
        slug,
        run_id,
        result.account_count,
        len(result.dcs),
        len(result.warnings),
        time.monotonic() - started,
    )
    return result


def _dc_problems(dcs: list[DcReport]) -> list[dict[str, str | None]]:
    """DCs, die nicht ``ok`` sind, als speicherbare Liste (nur technische Angaben)."""
    return [
        {"host": d.host, "status": d.status.value, "reason": d.reason}
        for d in dcs
        if d.status is not DcStatus.OK
    ]


def _store_results(
    factory: sessionmaker[Session],
    tenant_id: int,
    run_id: int,
    collection: AdCollection,
    notices: list[str],
) -> AdSyncResult:
    """Speichert Konten und schließt den Lauf in einer Transaktion ab."""
    warnings = collection.warnings + notices
    partial = any(w in _PARTIAL_WARNINGS for w in warnings)
    status = RunStatus.PARTIAL if partial else RunStatus.SUCCESS
    with factory.begin() as session:
        session.add_all(
            AdAccount(
                tenant_id=tenant_id,
                sync_run_id=run_id,
                object_guid=a.object_guid,
                sam_account_name=a.sam_account_name,
                user_principal_name=a.user_principal_name,
                mail=a.mail,
                user_account_control=a.user_account_control,
                when_created=a.when_created,
                pwd_last_set=a.pwd_last_set,
                last_logon=a.last_logon,
                last_logon_per_dc=a.last_logon_per_dc,
                last_logon_timestamp=a.last_logon_timestamp,
                account_expires=a.account_expires,
                description=a.description,
                distinguished_name=a.distinguished_name,
                ou=a.ou,
                member_of=a.member_of,
                master_data_incomplete=a.master_data_incomplete,
            )
            for a in collection.accounts
        )
        done = session.get(SyncRun, run_id)
        assert done is not None  # noqa: S101 - vom Aufrufer angelegt
        done.status = status
        done.finished_at = utcnow()
        done.account_count = len(collection.accounts)
        done.dcs_checked = len(collection.dcs)
        done.dcs_defective = _dc_problems(collection.dcs)
        done.warnings = warnings
    return AdSyncResult(
        run_id, status, len(collection.accounts), 0, warnings, None, list(collection.dcs)
    )


def _finish_failed(
    factory: sessionmaker[Session],
    run_id: int,
    slug: str,
    exc: Exception,
    dcs: list[DcReport],
    notices: list[str],
) -> AdSyncResult:
    # AdError/ConfigError/SyncError tragen nur feste Texte (Code bzw. Secret-Name), nie Inhalte.
    text = str(exc) if isinstance(exc, AdError | ConfigError | SyncError) else type(exc).__name__
    with factory.begin() as session:
        run = session.get(SyncRun, run_id)
        assert run is not None  # noqa: S101 - oben angelegt
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.error = text
        run.dcs_checked = len(dcs)
        run.dcs_defective = _dc_problems(dcs)
        run.warnings = notices
    log.error("AD-Abruf fehlgeschlagen: mandant=%s lauf=%d fehler=%s", slug, run_id, text)
    return AdSyncResult(run_id, RunStatus.FAILED, 0, 0, notices, text, dcs)
