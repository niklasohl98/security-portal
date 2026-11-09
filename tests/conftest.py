"""Gemeinsame Fixtures. Alle Daten sind erfunden; keine echten Mandantendaten."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
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
        sku_catalog_file=tmp_path / "kein-katalog.yaml",  # schnell und ohne Vorbelegung der Tabelle
        backup_dir=tmp_path / "backup",
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


class AuthedClient(TestClient):
    """TestClient mit angemeldeter Sitzung; ``csrf_token`` gehört zu dieser Sitzung."""

    csrf_token: str = ""


TEST_USER = "Tester"
TEST_PASSWORD = "Ein-gutes-Passwort-1"


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Friert die Uhr der Anwendung auf ``tests.identity_fakes.NOW`` ein und gibt sie zurück."""
    from tests.identity_fakes import NOW

    for module in (
        "app.core.backup",
        "app.auth.middleware",
        "app.auth.service",
        "app.auth.sso_runtime",
        "app.auth.sso_users",
        "app.backup.service",
        "app.backup.archive",
        "app.core.identities",
        "app.core.exceptions",
        "app.core.overrides",
        "app.core.review",
        "app.web.pages",
        "app.web.exceptions_page",
        "app.web.runs",
        "app.web.export",
    ):
        monkeypatch.setattr(f"{module}.utcnow", lambda: NOW)
    return NOW


@pytest.fixture
def anon(settings: Settings, frozen_clock: datetime) -> Iterator[TestClient]:
    """Die Anwendung im TestClient ohne Anmeldung (mit eingefrorener Uhr)."""
    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def site(settings: Settings, frozen_clock: datetime) -> Iterator[AuthedClient]:
    """Die Anwendung im TestClient, als ``Tester`` angemeldet, mit eingefrorener Uhr."""
    from sqlalchemy import delete

    from app.auth import service
    from app.auth.context import COOKIE_NAME
    from app.db.models import AppUser, AuditLog
    from app.main import create_app

    with AuthedClient(create_app(settings)) as client:
        factory = client.app.state.session_factory  # type: ignore[attr-defined]
        with factory.begin() as s:
            uid = service.create_user(s, TEST_USER, TEST_PASSWORD, "test", frozen_clock)
            tester = s.get_one(AppUser, uid)
            tester.must_change_password = False
            tester.is_admin = True  # sieht und nutzt die Benutzerverwaltung
            new = service.create_session(s, settings, uid, frozen_clock)
            s.execute(delete(AuditLog))  # Einträge vom Aufbau gehören nicht zu den Tests
        client.cookies.set(COOKIE_NAME, new.token)
        client.csrf_token = new.csrf_token
        yield client


@pytest.fixture(autouse=True)
def fast_password_hasher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Billiger Argon2-Hasher für die Tests (sonst kostet jeder Hash etwa 50 ms).

    Die echten Voreinstellungen prüft ``tests/test_auth_passwords.py`` gesondert.
    """
    from argon2 import PasswordHasher

    monkeypatch.setattr(
        "app.auth.passwords._hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
