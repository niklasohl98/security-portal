"""Konsistentes Wiederherstellungspaket mit Konfiguration, ohne private Schlüssel/Secrets."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from pathlib import Path

from app.config import ConfigError, Settings, load_tenants, resolve_ca_file
from app.core.backup import BackupError, create_backup, verify_backup
from app.db.models import utcnow
from app.db.session import migration_head

BUNDLE = "kontenwaechter.tar"


class ExternalBackupError(Exception):
    """Fester Fehlercode ohne Pfade, Zugangsdaten oder externe Ausgaben."""


def _copy_config(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    if b"PRIVATE KEY" in data:
        raise ExternalBackupError("config")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_bytes(data)
    os.chmod(destination, 0o600)


def create_bundle(settings: Settings, workspace: Path) -> Path:
    """Erzeugt eine geprüfte SQLite-Kopie plus benötigte nicht-geheime Konfiguration."""
    try:
        content = workspace / "content"
        content.mkdir(mode=0o700)
        made = create_backup(settings.database_url, content)
        (content / made.name).rename(content / "database.db")
        _copy_config(settings.tenants_file, content / "config" / "tenants.yaml")
        if settings.sku_names_file.is_file():
            _copy_config(settings.sku_names_file, content / "config" / "sku_names.yaml")
        for tenant in load_tenants(settings.tenants_file):
            if tenant.ad and tenant.ad.ca_file:
                source = resolve_ca_file(settings.tenants_file, tenant.ad.ca_file)
                _copy_config(source, content / "config" / tenant.ad.ca_file)
        if settings.external_backup and settings.external_backup.ca_file:
            ca = settings.external_backup.ca_file
            _copy_config(resolve_ca_file(settings.tenants_file, ca), content / "config" / ca)
        if settings.external_backup and settings.external_backup.ssh_secret:
            _copy_config(
                settings.tenants_file.parent / "backup_known_hosts",
                content / "config" / "backup_known_hosts",
            )
        (content / "manifest.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "created_at": utcnow().isoformat(),
                    "schema": migration_head(),
                    "database": "database.db",
                    "secrets_included": False,
                }
            ),
            encoding="utf-8",
        )
        bundle = workspace / BUNDLE
        with tarfile.open(bundle, "w") as archive:
            for path in sorted(content.rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=str(path.relative_to(content)), recursive=False)
        os.chmod(bundle, 0o600)
        return bundle
    except (OSError, ConfigError, BackupError):
        raise ExternalBackupError("bundle") from None


def unpack_bundle(bundle: Path, target: Path) -> Path:
    """Entpackt nur reguläre Dateien in ein neues Ziel und prüft die wiederhergestellte DB.

    Kein Überschreiben bestehender Verzeichnisse, keine Links, keine Pfade außerhalb des Ziels.
    Bei Fehlern wird nur das von dieser Funktion neu erstellte Ziel entfernt.
    """
    created = False
    try:
        target.mkdir(mode=0o700, parents=False, exist_ok=False)
        created = True
        with tarfile.open(bundle, "r") as archive:
            for member in archive:
                relative = Path(member.name)
                if (
                    not member.isfile()
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or not (
                        member.name in {"database.db", "manifest.json"}
                        or member.name.startswith("config/")
                    )
                ):
                    raise ExternalBackupError("archive")
                destination = target / relative
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ExternalBackupError("archive")
                with source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(destination, 0o600)
        manifest = json.loads((target / "manifest.json").read_text())
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != 1
            or manifest.get("database") != "database.db"
        ):
            raise ExternalBackupError("archive")
        load_tenants(target / "config" / "tenants.yaml")
        verify_backup(target / "database.db")
        return target / "database.db"
    except (OSError, tarfile.TarError, ValueError, ConfigError, BackupError, ExternalBackupError):
        if created:
            shutil.rmtree(target)
        raise ExternalBackupError("restore") from None
