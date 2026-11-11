"""Abruf je Mandant und Quelle: Lauf anlegen, Daten holen, speichern, Status setzen.

Ein Lauf je (Mandant, Quelle) gleichzeitig: eine Sperre im Prozess verhindert Doppelläufe
(der Scheduler kommt in Phase 4). Fehler beim Abruf werden im Lauf als ``failed`` festgehalten
und nicht weitergeworfen, damit ein Zeitplan nie durch einen Mandanten stirbt. Der Fehlertext
enthält nur Fehlerart, Statuscode und Fehlercode, nie Inhalte.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, read_secret
from app.connectors.graph import (
    GraphClient,
    GraphError,
    MsalCertificateTokenProvider,
    SkuInfo,
    UsersResult,
    fetch_skus,
    fetch_users,
)
from app.core.source_references import record_entra_accounts
from app.db.models import EntraAccount, RunStatus, Source, SyncRun, Tenant, TenantSku, utcnow

log = logging.getLogger(__name__)

WARN_NO_SIGN_IN = "sign_in_activity_unavailable"
WARN_SOURCE_REFERENCES_INCOMPLETE = "entra_source_references_incomplete"

_locks: dict[tuple[int, Source], threading.Lock] = {}
_locks_guard = threading.Lock()


class SyncError(Exception):
    """Lauf nicht startbar: Mandant fehlt, ist deaktiviert oder die Quelle nicht konfiguriert."""


class SyncBusyError(SyncError):
    """Für diesen Mandanten und diese Quelle läuft bereits ein Abruf."""


@dataclass(frozen=True)
class SyncResult:
    """Kurzfassung eines Laufs für CLI und Oberfläche."""

    run_id: int
    status: RunStatus
    account_count: int
    sku_count: int
    warnings: list[str]
    error: str | None


def _lock_for(tenant_id: int, source: Source) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault((tenant_id, source), threading.Lock())


def fail_stale_runs(session: Session) -> int:
    """Markiert beim Start Läufe, die noch ``running`` sind, als ``failed`` (Neustart/Absturz)."""
    stale = list(session.scalars(select(SyncRun).where(SyncRun.status == RunStatus.RUNNING)))
    for run in stale:
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.error = "abgebrochen (Neustart)"
    return len(stale)


def build_graph_client(tenant: Tenant, secrets_dir: Path) -> GraphClient:
    """Baut den Graph-Client für einen Mandanten (privater Schlüssel aus dem Docker-Secret)."""
    if not (
        tenant.entra_tenant_id
        and tenant.entra_client_id
        and tenant.entra_certificate_secret
        and tenant.entra_certificate_thumbprint
    ):
        raise SyncError("Entra ist für diesen Mandanten nicht konfiguriert")
    pem = read_secret(tenant.entra_certificate_secret, secrets_dir)
    provider = MsalCertificateTokenProvider(
        tenant.entra_tenant_id,
        tenant.entra_client_id,
        pem,
        tenant.entra_certificate_thumbprint,
    )
    return GraphClient(provider)


def _describe(exc: Exception) -> str:
    """Fehlertext ohne Inhalte: Art, bei Graph zusätzlich Status und Code."""
    if isinstance(exc, GraphError):
        return f"GraphError status={exc.status} code={exc.code}"
    return type(exc).__name__


def run_entra_sync(
    factory: sessionmaker[Session],
    slug: str,
    secrets_dir: Path,
    *,
    client: GraphClient | None = None,
) -> SyncResult:
    """Ruft Benutzer und Lizenzen eines Mandanten aus Entra ab und speichert sie.

    Args:
        factory: Sitzungsfabrik.
        slug: Kürzel des Mandanten.
        secrets_dir: Verzeichnis der Docker-Secrets.
        client: Fertiger Client (Tests); Standard ist ``build_graph_client``.

    Returns:
        Ergebnis des Laufs. ``failed`` bei Fehlern im Abruf (nicht als Exception).

    Raises:
        SyncError: Mandant unbekannt, deaktiviert oder ohne Entra-Konfiguration.
        SyncBusyError: Es läuft schon ein Abruf für diesen Mandanten.
    """
    with factory() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
        if tenant is None or not tenant.enabled:
            raise SyncError("Mandant unbekannt oder deaktiviert")
        if tenant.entra_tenant_id is None:
            raise SyncError("Entra ist für diesen Mandanten nicht konfiguriert")
        tenant_id = tenant.id

    lock = _lock_for(tenant_id, Source.ENTRA)
    if not lock.acquire(blocking=False):
        raise SyncBusyError("Abruf läuft bereits")
    try:
        return _run_locked(factory, tenant_id, slug, secrets_dir, client)
    finally:
        lock.release()


def _run_locked(
    factory: sessionmaker[Session],
    tenant_id: int,
    slug: str,
    secrets_dir: Path,
    client: GraphClient | None,
) -> SyncResult:
    started = time.monotonic()
    with factory.begin() as session:
        run = SyncRun(tenant_id=tenant_id, source=Source.ENTRA, status=RunStatus.RUNNING)
        session.add(run)
        session.flush()
        run_id = run.id
    log.info("Entra-Abruf gestartet: mandant=%s lauf=%d", slug, run_id)

    try:
        with factory() as session:
            tenant = session.get(Tenant, tenant_id)
            assert tenant is not None  # noqa: S101 - oben geprüft
            directory_id = tenant.entra_tenant_id
        if client is None:
            client = build_graph_client(tenant, secrets_dir)
        skus = fetch_skus(client)
        users = fetch_users(client)
    except (GraphError, ConfigError, SyncError) as exc:
        return _finish_failed(factory, run_id, slug, exc)
    except Exception as exc:  # noqa: BLE001 - ein unerwarteter Fehler darf den Zeitplan nicht beenden
        return _finish_failed(factory, run_id, slug, exc)

    result = _store_results(factory, tenant_id, run_id, users, skus, directory_id)
    log.info(
        "Entra-Abruf fertig: mandant=%s lauf=%d konten=%d skus=%d warnungen=%d dauer=%.1fs",
        slug,
        run_id,
        result.account_count,
        result.sku_count,
        len(result.warnings),
        time.monotonic() - started,
    )
    return result


def _store_results(
    factory: sessionmaker[Session],
    tenant_id: int,
    run_id: int,
    users: UsersResult,
    skus: list[SkuInfo],
    directory_id: str | None,
) -> SyncResult:
    """Speichert Konten und SKUs und schließt den Lauf in einer Transaktion ab."""
    warnings = [] if users.sign_in_available else [WARN_NO_SIGN_IN]
    with factory.begin() as session:
        complete = record_entra_accounts(
            session, tenant_id, directory_id or "", (u.object_id for u in users.users)
        )
        if not complete:
            warnings.append(WARN_SOURCE_REFERENCES_INCOMPLETE)
        status = RunStatus.PARTIAL if warnings else RunStatus.SUCCESS
        session.add_all(
            EntraAccount(
                tenant_id=tenant_id,
                sync_run_id=run_id,
                object_id=u.object_id,
                user_principal_name=u.user_principal_name,
                display_name=u.display_name,
                mail=u.mail,
                user_type=u.user_type,
                account_enabled=u.account_enabled,
                created_at=u.created_at,
                on_prem_sync_enabled=u.on_prem_sync_enabled,
                on_prem_immutable_id=u.on_prem_immutable_id,
                on_prem_sam_account_name=u.on_prem_sam_account_name,
                last_successful_sign_in=u.last_successful_sign_in,
                last_non_interactive_sign_in=u.last_non_interactive_sign_in,
                last_sign_in=u.last_sign_in,
                licenses=u.licenses,
            )
            for u in users.users
        )
        session.add_all(
            TenantSku(
                tenant_id=tenant_id,
                sync_run_id=run_id,
                sku_id=s.sku_id,
                part_number=s.part_number,
                applies_to=s.applies_to,
                capability_status=s.capability_status,
                enabled_units=s.enabled_units,
                consumed_units=s.consumed_units,
            )
            for s in skus
        )
        done = session.get(SyncRun, run_id)
        assert done is not None  # noqa: S101 - vom Aufrufer angelegt
        done.status = status
        done.finished_at = utcnow()
        done.account_count = len(users.users)
        done.warnings = warnings
    return SyncResult(run_id, status, len(users.users), len(skus), warnings, None)


def _finish_failed(
    factory: sessionmaker[Session], run_id: int, slug: str, exc: Exception
) -> SyncResult:
    text = _describe(exc)
    with factory.begin() as session:
        run = session.get(SyncRun, run_id)
        assert run is not None  # noqa: S101 - oben angelegt
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.error = text
    log.error("Entra-Abruf fehlgeschlagen: mandant=%s lauf=%d fehler=%s", slug, run_id, text)
    return SyncResult(run_id, RunStatus.FAILED, 0, 0, [], text)
