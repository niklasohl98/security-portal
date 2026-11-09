"""Externe Sicherung bewusst einrichten, prüfen und in ein neues Verzeichnis zurückholen."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.backup.service import run_external_backup
from app.config import Settings
from app.db.session import create_db_engine, make_session_factory


def add_commands(sub: argparse._SubParsersAction[Any]) -> None:
    """Registriert eigenständige Befehle ohne automatische Initialisierung des Ziels."""
    for action in ("init", "check", "run", "prune", "restore"):
        parser = sub.add_parser(f"external-backup-{action}", help=f"Externe Sicherung: {action}")
        if action == "prune":
            parser.add_argument(
                "--yes", action="store_true", help="Aufbewahrung/Löschung bestätigen"
            )
        if action == "restore":
            parser.add_argument("target", help="Neues, noch nicht vorhandenes Restore-Verzeichnis")
            parser.add_argument("--snapshot", default="latest", help="Snapshot-ID oder latest")


def external_backup_cmd(settings: Settings, args: argparse.Namespace) -> int:
    """Meldet nur Status, nie Originalausgaben von Restic, Ziele oder Zugangsdaten."""
    if settings.external_backup is None:
        print("Externe Sicherung ist deaktiviert.")
        return 2
    action = args.command.removeprefix("external-backup-")
    action = "backup" if action == "run" else action
    engine = create_db_engine(settings.database_url)
    try:
        ok = run_external_backup(
            make_session_factory(engine),
            settings,
            "cli",
            action=action,
            target=Path(args.target) if getattr(args, "target", None) else None,
            snapshot=getattr(args, "snapshot", "latest"),
            confirm_prune=getattr(args, "yes", False),
        )
    finally:
        engine.dispose()
    print(
        "Externe Sicherungsaktion abgeschlossen."
        if ok
        else "FEHLER: Externe Sicherungsaktion fehlgeschlagen (Code im Audit-Log)."
    )
    return 0 if ok else 1
