"""Sicherung der SQLite-Datenbank: nächtlich, geprüft, mit Aufbewahrung und Wiederherstellung.

Die Datenbank enthält Dinge, die sich nicht aus Entra und AD neu lesen lassen: Ausnahmen,
Audit-Log, Entscheidungen der Prüfliste, Benutzer. Gesichert wird mit der Online-Backup-API von
SQLite (``sqlite3.Connection.backup``): konsistent, auch während die Anwendung schreibt (WAL).
Jede Sicherung wird vor dem Umbenennen geprüft (``PRAGMA integrity_check`` und die erwarteten
Tabellen); eine unvollständige Datei bleibt nie unter dem endgültigen Namen liegen. Dateien sind
nur für den Benutzer des Containers lesbar (0600), da sie Namen und Passwort-Hashes enthalten.

Aufbewahrung: Sicherungen älter als ``backup_keep_days`` werden gelöscht, die neueste bleibt
immer. Es werden nur Dateien mit den eigenen Namensmustern angefasst. Liegt ``backup_dir`` auf
demselben Gerät wie die Daten, schützt die Sicherung nicht vor einem Geräteausfall (KI-6).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import AuditLog, utcnow

log = logging.getLogger(__name__)

PREFIX = "kontenwaechter-"
PRE_MIGRATION_PREFIX = "vor-migration-"
SUFFIX = ".db"
_REQUIRED_TABLES = frozenset({"alembic_version", "tenant", "audit_log"})
ACTOR_SYSTEM = "system"


class BackupError(Exception):
    """Sicherung oder Wiederherstellung nicht möglich. ``code`` ist ein fester Text.

    Codes: ``not_sqlite`` (andere Datenbank), ``no_db`` (Datei fehlt), ``dir`` (Verzeichnis nicht
    nutzbar), ``verify`` (Prüfung fehlgeschlagen), ``io`` (Lese-/Schreibfehler), ``no_file``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BackupFile:
    """Eine Sicherungsdatei im Verzeichnis."""

    name: str
    size: int
    created_at: datetime  # Änderungszeit der Datei (UTC)
    pre_migration: bool  # vor einer Datenbankmigration angelegt


@dataclass(frozen=True)
class BackupStatus:
    """Zustand der Sicherungen für die Oberfläche. ``state``: ``ok``, ``never``, ``stale``,
    ``failed`` oder ``unsupported`` (keine SQLite-Datenbank)."""

    state: str
    last_ok_at: datetime | None
    last_ok_name: str | None
    last_size: int | None


def sqlite_path(database_url: str) -> Path | None:
    """Pfad der SQLite-Datei zur URL; ``None`` bei anderen Datenbanken oder ``:memory:``."""
    try:
        url = make_url(database_url)
    except ArgumentError:
        return None
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def _name_for(prefix: str, now: datetime) -> str:
    return f"{prefix}{now.astimezone(UTC):%Y%m%d-%H%M%S}{SUFFIX}"


def _remove_with_sidecars(path: Path) -> None:
    """Löscht eine Datei samt SQLite-Nebendateien (``-wal``, ``-shm``, ``-journal``)."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def verify_backup(path: Path) -> None:
    """Prüft eine Sicherungsdatei: lesbar, ``integrity_check`` = ok, erwartete Tabellen vorhanden.

    Raises:
        BackupError: ``no_file`` oder ``verify``.
    """
    if not path.is_file():
        raise BackupError("no_file")
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchall()
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
    except sqlite3.DatabaseError:
        raise BackupError("verify") from None
    if result != [("ok",)] or not tables >= _REQUIRED_TABLES:
        raise BackupError("verify")


def create_backup(
    database_url: str,
    backup_dir: Path,
    now: datetime | None = None,
    prefix: str = PREFIX,
) -> BackupFile:
    """Legt eine geprüfte Sicherung der Datenbank im Verzeichnis ab.

    Raises:
        BackupError: siehe ``BackupError``. Bei jedem Fehler bleibt keine Teildatei zurück.
    """
    now = now or utcnow()
    source = sqlite_path(database_url)
    if source is None:
        raise BackupError("not_sqlite")
    if not source.is_file():
        raise BackupError("no_db")
    try:
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise BackupError("dir") from None
    if not os.access(backup_dir, os.W_OK):
        raise BackupError("dir")
    final = backup_dir / _name_for(prefix, now)
    tmp = final.with_name(final.name + ".tmp")
    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(tmp)) as dest:
            src.backup(dest)
            # Das Original läuft im WAL-Modus und die Sicherung erbt ihn. Ohne diese Umstellung
            # blieben ``-wal``/``-shm`` neben der Datei liegen; so ist sie genau eine Datei.
            dest.execute("PRAGMA journal_mode=DELETE")
        verify_backup(tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except (sqlite3.Error, OSError):
        _remove_with_sidecars(tmp)
        raise BackupError("io") from None
    except BackupError:
        _remove_with_sidecars(tmp)
        raise
    stat = final.stat()
    return BackupFile(
        final.name,
        stat.st_size,
        datetime.fromtimestamp(stat.st_mtime, UTC),
        prefix == PRE_MIGRATION_PREFIX,
    )


def list_backups(backup_dir: Path) -> list[BackupFile]:
    """Alle Sicherungen im Verzeichnis, neueste zuerst (nur eigene Namensmuster)."""
    if not backup_dir.is_dir():
        return []
    found = []
    for path in backup_dir.iterdir():
        name = path.name
        pre = name.startswith(PRE_MIGRATION_PREFIX)
        if not (name.endswith(SUFFIX) and (pre or name.startswith(PREFIX)) and path.is_file()):
            continue
        stat = path.stat()
        found.append(
            BackupFile(name, stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC), pre)
        )
    return sorted(found, key=lambda b: (b.created_at, b.name), reverse=True)


def prune_backups(backup_dir: Path, keep_days: int, now: datetime | None = None) -> int:
    """Löscht Sicherungen älter als ``keep_days`` Tage; die neueste bleibt. Gibt die Zahl zurück.

    Raises:
        ValueError: bei ``keep_days < 1``.
    """
    if keep_days < 1:
        raise ValueError("keep_days muss mindestens 1 sein")
    cutoff = (now or utcnow()) - timedelta(days=keep_days)
    files = list_backups(backup_dir)
    deleted = 0
    for item in files[1:]:  # files[0] ist die neueste und bleibt
        if item.created_at < cutoff:
            (backup_dir / item.name).unlink(missing_ok=True)
            deleted += 1
    return deleted


def restore_backup(source: Path, database_url: str, now: datetime | None = None) -> Path:
    """Stellt eine Sicherung als Datenbank wieder her. Die Anwendung muss gestoppt sein.

    Die Sicherung wird vorher geprüft. Die bisherige Datei bleibt als ``<name>.vor-restore-<Zeit>``
    erhalten (samt veralteter ``-wal``/``-shm``-Dateien, die entfernt werden). Gibt den Pfad der
    beiseitegelegten Datei zurück (oder der neuen Datei, wenn es keine alte gab).

    Raises:
        BackupError: ``verify``, ``no_file``, ``not_sqlite`` oder ``io``.
    """
    target = sqlite_path(database_url)
    if target is None:
        raise BackupError("not_sqlite")
    verify_backup(source)
    now = now or utcnow()
    aside = target.with_name(f"{target.name}.vor-restore-{now.astimezone(UTC):%Y%m%d-%H%M%S}")
    tmp = target.with_name(target.name + ".restore.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, tmp)
        os.chmod(tmp, 0o600)
        if target.exists():
            os.replace(target, aside)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(tmp, target)
        verify_backup(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise BackupError("io") from None
    return aside if aside.exists() else target


def migration_pending(database_url: str, head: str) -> bool:
    """Ob die Datenbankdatei existiert, schon Migrationen hat und nicht auf ``head`` steht."""
    path = sqlite_path(database_url)
    if path is None or not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None and row[0] != head


def backup_before_migration(settings: Settings, head: str) -> BackupFile | None:
    """Sichert vor einer anstehenden Migration.

    Scheitert die Sicherung, startet die Anwendung trotzdem (eine falsch eingerichtete Sicherung
    soll den Dienst nicht blockieren), aber mit einer Fehlerzeile im Log.
    """
    if not migration_pending(settings.database_url, head):
        return None
    try:
        made = create_backup(
            settings.database_url, settings.backup_dir, prefix=PRE_MIGRATION_PREFIX
        )
    except BackupError as exc:
        log.error("Sicherung vor der Migration fehlgeschlagen code=%s", exc.code)
        return None
    log.warning("Sicherung vor der Migration angelegt groesse=%s", made.size)
    return made


def _audit(session: Session, action: str, detail: str, now: datetime) -> None:
    session.add(AuditLog(tenant_id=None, at=now, actor=ACTOR_SYSTEM, action=action, detail=detail))


def run_backup(
    factory: sessionmaker[Session], settings: Settings, actor: str = ACTOR_SYSTEM
) -> BackupFile | None:
    """Sicherung plus Aufbewahrung; Ergebnis im Audit-Log (``backup.ok``/``backup.failed``).

    Wirft nie: ein Fehler wird geloggt und eingetragen, damit der Zeitplan weiterläuft. Gibt die
    neue Datei zurück oder ``None`` bei Fehlschlag.
    """
    now = utcnow()
    try:
        made = create_backup(settings.database_url, settings.backup_dir, now)
        removed = prune_backups(settings.backup_dir, settings.backup_keep_days, now)
    except BackupError as exc:
        log.error("Sicherung fehlgeschlagen code=%s", exc.code)
        with factory.begin() as session:
            session.add(
                AuditLog(
                    tenant_id=None,
                    at=now,
                    actor=actor,
                    action="backup.failed",
                    detail=f"code={exc.code}",
                )
            )
        return None
    with factory.begin() as session:
        session.add(
            AuditLog(
                tenant_id=None,
                at=now,
                actor=actor,
                action="backup.ok",
                detail=f"datei={made.name} groesse={made.size} geloescht={removed}",
            )
        )
    log.info("Sicherung angelegt groesse=%s geloescht=%s", made.size, removed)
    return made


def backup_status(
    session: Session, settings: Settings, now: datetime | None = None
) -> BackupStatus:
    """Zustand für die Oberfläche: ``ok``, ``never`` (noch keine), ``stale`` (zu alt), ``failed``
    (letzter Versuch fehlgeschlagen, neuer als die letzte gelungene) oder ``unsupported``."""
    now = now or utcnow()
    if sqlite_path(settings.database_url) is None:
        return BackupStatus("unsupported", None, None, None)
    regular = [b for b in list_backups(settings.backup_dir) if not b.pre_migration]
    last = regular[0] if regular else None
    last_ok = last.created_at if last else None
    failed_at = session.scalar(
        select(AuditLog.at)
        .where(AuditLog.action == "backup.failed")
        .order_by(AuditLog.at.desc())
        .limit(1)
    )
    if failed_at is not None and (last_ok is None or failed_at > last_ok):
        state = "failed"
    elif last is None:
        state = "never"
    elif now - last.created_at > timedelta(hours=settings.backup_warn_hours):
        state = "stale"
    else:
        state = "ok"
    return BackupStatus(state, last_ok, last.name if last else None, last.size if last else None)
