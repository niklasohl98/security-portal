"""Gemeinsame Fixtures. Alle Daten sind erfunden; keine echten Mandantendaten."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, load_tenants
from app.core.tenants import sync_tenants
from app.db.session import create_db_engine, make_session_factory, run_migrations

EXAMPLE_TENANTS = Path(__file__).parent.parent / "config" / "tenants.example.yaml"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Einstellungen mit temporärer SQLite-Datei und der Beispiel-Mandantendatei."""
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        tenants_file=EXAMPLE_TENANTS,
        secrets_dir=tmp_path / "secrets",
        sku_names_file=tmp_path / "keine-sku-datei.yaml",
        log_level="WARNING",
    )


@pytest.fixture
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Migrierte Datenbank mit den Beispielmandanten (cg, cvh); liefert die Sitzungsfabrik."""
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    f = make_session_factory(engine)
    with f.begin() as s:
        sync_tenants(s, load_tenants(EXAMPLE_TENANTS))
    yield f
    engine.dispose()


@pytest.fixture
def site(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Die Anwendung im TestClient mit eingefrorener Uhr (``tests.identity_fakes.NOW``)."""
    from app.main import create_app
    from tests.identity_fakes import NOW

    for module in (
        "app.core.identities",
        "app.core.exceptions",
        "app.core.overrides",
        "app.core.review",
        "app.web.pages",
        "app.web.exceptions_page",
        "app.web.runs",
    ):
        monkeypatch.setattr(f"{module}.utcnow", lambda: NOW)
    with TestClient(create_app(settings)) as client:
        yield client
