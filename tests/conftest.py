"""Gemeinsame Fixtures. Alle Daten sind erfunden; keine echten Mandantendaten."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

EXAMPLE_TENANTS = Path(__file__).parent.parent / "config" / "tenants.example.yaml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Einstellungen mit temporärer SQLite-Datei und der Beispiel-Mandantendatei."""
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        tenants_file=EXAMPLE_TENANTS,
        secrets_dir=tmp_path / "secrets",
        log_level="WARNING",
    )
