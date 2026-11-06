from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.config import TenantConfig, load_tenants
from app.core.tenants import sync_tenants
from app.db.models import (
    AccountException,
    AuditLog,
    Base,
    Identity,
    RunStatus,
    Source,
    SyncRun,
    Tenant,
)
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.conftest import EXAMPLE_TENANTS


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_migrations(url)
    return url


def test_migrations_create_all_tables(db_url: str) -> None:
    engine = create_db_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    expected = {t.name for t in Base.metadata.sorted_tables}
    assert expected <= tables
    assert {"tenant", "sync_run", "entra_account", "ad_account", "identity"} <= expected
    engine.dispose()


def test_migrations_are_idempotent_and_record_version(db_url: str) -> None:
    """Regression: ``alembic_version`` wurde nicht committet, der zweite Start schlug fehl."""
    run_migrations(db_url)  # zweiter Lauf auf derselben Datenbank darf nichts mehr tun
    engine = create_db_engine(db_url)
    with engine.connect() as conn:
        versions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    engine.dispose()
    assert len(versions) == 1


def test_models_and_migrations_are_in_sync(db_url: str) -> None:
    """Schlägt fehl, wenn ein Modell geändert wurde, ohne eine Migration zu schreiben."""
    engine = create_db_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_every_customer_table_has_tenant_id() -> None:
    global_tables = {"tenant", "sku_name", "app_user", "app_session"}
    for table in Base.metadata.sorted_tables:
        if table.name not in global_tables:
            assert "tenant_id" in table.c, table.name


def test_datetimes_roundtrip_as_utc_aware(db_url: str) -> None:
    engine = create_db_engine(db_url)
    factory = make_session_factory(engine)
    cet = timezone(timedelta(hours=1))
    with factory.begin() as s:
        s.add(Tenant(slug="x", name="X"))
    with factory.begin() as s:
        t = s.scalars(select(Tenant)).one()
        s.add(
            SyncRun(
                tenant_id=t.id,
                source=Source.AD,
                status=RunStatus.RUNNING,
                started_at=datetime(2026, 1, 1, 12, 0, tzinfo=cet),
            )
        )
    with factory() as s:
        run = s.scalars(select(SyncRun)).one()
        assert run.started_at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        assert run.started_at.tzinfo is not None
        assert run.source is Source.AD
    engine.dispose()


def test_naive_datetime_is_rejected(db_url: str) -> None:
    engine = create_db_engine(db_url)
    with make_session_factory(engine)() as s:
        s.add(AuditLog(actor="a", action="x", at=datetime(2026, 1, 1)))  # noqa: DTZ001
        with pytest.raises(StatementError, match="Zeitzone"):
            s.flush()
    engine.dispose()


def test_foreign_keys_are_enforced_and_cascade(db_url: str) -> None:
    engine = create_db_engine(db_url)
    factory = make_session_factory(engine)
    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(SyncRun(tenant_id=999, source=Source.ENTRA, status=RunStatus.RUNNING))
    with factory.begin() as s:
        t = Tenant(slug="c", name="C")
        s.add(t)
        s.flush()
        ident = Identity(tenant_id=t.id, key="k")
        s.add(ident)
        s.flush()
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=ident.id,
                reason="Break-Glass",
                created_by="admin",
                valid_until=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )
    with factory.begin() as s:
        s.delete(s.scalars(select(Tenant)).one())
    with factory() as s:
        assert s.execute(text("SELECT count(*) FROM exception")).scalar_one() == 0
    engine.dispose()


def test_invalid_enum_value_is_rejected_by_database(db_url: str) -> None:
    engine = create_db_engine(db_url)
    with make_session_factory(engine).begin() as s:
        s.add(Tenant(slug="q", name="Q"))
    with engine.begin() as conn:
        stmt = text(
            "INSERT INTO sync_run (tenant_id, source, started_at, status, account_count, "
            "dcs_checked, dcs_defective) VALUES (1, 'bogus', '2026-01-01', 'running', 0, 0, '[]')"
        )
        with pytest.raises(IntegrityError):
            conn.execute(stmt)
    engine.dispose()


def _sync(db_url: str, configs: list[TenantConfig]) -> list[Tenant]:
    engine = create_db_engine(db_url)
    factory = make_session_factory(engine)
    with factory.begin() as s:
        sync_tenants(s, configs)
    with factory() as s:
        rows = list(s.scalars(select(Tenant).order_by(Tenant.slug)))
    engine.dispose()
    return rows


def test_sync_tenants_creates_cg_and_cvh(db_url: str) -> None:
    rows = _sync(db_url, load_tenants(EXAMPLE_TENANTS))
    assert [(r.slug, r.enabled) for r in rows] == [("cg", True), ("cvh", True)]
    cg = rows[0]
    assert cg.entra_certificate_secret == "cg_entra_cert"  # nur der Name, kein Inhalt
    assert cg.ad_bind_secret == "cg_ad_bind"
    assert cg.inactive_days == 30


def test_sync_tenants_is_idempotent_updates_and_disables(db_url: str) -> None:
    configs = load_tenants(EXAMPLE_TENANTS)
    _sync(db_url, configs)
    changed = [configs[0].model_copy(update={"name": "CG neu"})]  # cvh fehlt jetzt
    rows = _sync(db_url, changed)
    by_slug = {r.slug: r for r in rows}
    assert by_slug["cg"].name == "CG neu"
    assert by_slug["cvh"].enabled is False  # deaktiviert, nicht gelöscht
    assert len(rows) == 2
