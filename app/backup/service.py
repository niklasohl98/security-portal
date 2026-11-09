"""Externe Sicherung: manuelle Einrichtung, täglicher Job, Status und isolierter Restore."""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.backup.archive import BUNDLE, ExternalBackupError, create_bundle, unpack_bundle
from app.backup.restic import run_restic
from app.config import Settings
from app.db.models import AuditLog, utcnow

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_SELECTOR = ["--tag", "kontenwaechter", "--host", "kontenwaechter"]
_ACTIONS = frozenset({"backup", "init", "check", "restore", "prune"})


def _target_marker(settings: Settings) -> str:
    cfg = settings.external_backup
    repository = cfg.repository if cfg else ""
    return "target=" + hashlib.sha256(repository.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class ExternalBackupStatus:
    """Deaktiviert, nie gesichert, erfolgreich, überfällig oder fehlgeschlagen."""

    state: str
    last_ok_at: datetime | None = None
    last_check_at: datetime | None = None
    last_restore_at: datetime | None = None


def external_status(
    session: Session,
    settings: Settings,
    now: datetime | None = None,
) -> ExternalBackupStatus:
    """Status aus Audit-Zeitpunkten; deaktivierte Ziele erzeugen keine Fehlwarnung."""
    cfg = settings.external_backup
    if cfg is None:
        return ExternalBackupStatus("disabled")

    def latest(action: str) -> datetime | None:
        return session.scalar(
            select(AuditLog.at)
            .where(AuditLog.action == action, AuditLog.detail.startswith(_target_marker(settings)))
            .order_by(AuditLog.at.desc())
            .limit(1)
        )

    ok, failed = latest("external_backup.ok"), latest("external_backup.failed")
    state = "ok"
    if failed and (ok is None or failed >= ok):
        state = "failed"
    elif ok is None:
        state = "never"
    elif (now or utcnow()) - ok > timedelta(hours=cfg.warn_hours):
        state = "stale"
    return ExternalBackupStatus(
        state, ok, latest("external_backup.check_ok"), latest("external_backup.restore_ok")
    )


def execute(
    settings: Settings,
    action: str,
    *,
    target: Path | None = None,
    snapshot: str = "latest",
    confirm_prune: bool = False,
) -> Path | None:
    """Führt eine ausdrücklich gewählte Aktion aus; keine automatische Initialisierung/Löschung.

    Restore erzeugt nur ein neues Verzeichnis, nie die laufende Datenbank. Für einen fremden
    Repository-Snapshot sind nur dessen feste ID oder ``latest`` erlaubt.
    """
    cfg = settings.external_backup
    if cfg is None:
        raise ExternalBackupError("disabled")
    if action not in _ACTIONS:
        raise ExternalBackupError("action")
    if not _LOCK.acquire(blocking=False):
        raise ExternalBackupError("busy")
    try:
        if action == "init":
            run_restic(settings, cfg, ["init"])
        elif action == "check":
            run_restic(settings, cfg, ["check", "--read-data"])
        elif action == "prune":
            if not confirm_prune:
                raise ExternalBackupError("confirmation")
            run_restic(
                settings,
                cfg,
                [
                    "forget",
                    *_SELECTOR,
                    "--group-by",
                    "host,tags",
                    "--keep-daily",
                    str(cfg.keep_daily),
                    "--keep-weekly",
                    str(cfg.keep_weekly),
                    "--keep-monthly",
                    str(cfg.keep_monthly),
                    "--prune",
                ],
            )
        else:
            settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="external-backup-", dir=settings.data_dir
            ) as tmp:
                workspace = Path(tmp)
                if action == "backup":
                    bundle = create_bundle(settings, workspace)
                    run_restic(
                        settings,
                        cfg,
                        ["backup", *_SELECTOR, "--stdin", "--stdin-filename", BUNDLE],
                        bundle,
                    )
                else:
                    if target is None or target.exists() or target.is_symlink():
                        raise ExternalBackupError("target")
                    if snapshot != "latest" and not re.fullmatch(r"[a-f0-9]{8,64}", snapshot):
                        raise ExternalBackupError("snapshot")
                    run_restic(
                        settings, cfg, ["restore", snapshot, *_SELECTOR, "--target", str(workspace)]
                    )
                    return unpack_bundle(workspace / BUNDLE, target)
    except OSError:
        raise ExternalBackupError("io") from None
    finally:
        _LOCK.release()
    return None


def run_external_backup(
    factory: sessionmaker[Session],
    settings: Settings,
    actor: str = "system",
    *,
    action: str = "backup",
    target: Path | None = None,
    snapshot: str = "latest",
    confirm_prune: bool = False,
) -> bool:
    """Auditierter Job; Fehler beeinträchtigen weder lokale Backups noch andere Zeitplan-Jobs."""
    if settings.external_backup is None:
        return False
    try:
        execute(settings, action, target=target, snapshot=snapshot, confirm_prune=confirm_prune)
    except ExternalBackupError as exc:
        log.error("Externe Sicherungsaktion fehlgeschlagen code=%s", str(exc))
        with factory.begin() as session:
            session.add(
                AuditLog(
                    actor=actor,
                    action="external_backup.failed",
                    detail=f"{_target_marker(settings)} operation={action} code={exc}",
                    at=utcnow(),
                )
            )
        return False
    suffix = "ok" if action == "backup" else f"{action}_ok"
    with factory.begin() as session:
        session.add(
            AuditLog(
                actor=actor,
                action=f"external_backup.{suffix}",
                detail=_target_marker(settings),
                at=utcnow(),
            )
        )
    log.info("Externe Sicherungsaktion abgeschlossen operation=%s", action)
    return True
