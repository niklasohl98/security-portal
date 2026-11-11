from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_tenants
from app.core.retention import purge_raw_data
from app.core.tenants import sync_tenants
from app.db.models import (
    AccountException,
    AuditLog,
    DirectorySource,
    Identity,
    Source,
    SourceAccount,
)
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.conftest import EXAMPLE_TENANTS
from tests.identity_fakes import NOW, get_tenant, make_run, seed_world

DIRECTORY_ID = "00000000-0000-4000-8000-000000000001"
OBJECT_ID = "00000000-0000-4000-8000-000000000002"


def _migration_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "app/db/migrations")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _snapshot(engine: Engine, tables: list[str]) -> dict[str, list[tuple[object, ...]]]:
    metadata = MetaData()
    metadata.reflect(engine, only=tables)
    with engine.connect() as conn:
        return {
            name: [tuple(row) for row in conn.execute(select(metadata.tables[name]))]
            for name in tables
        }


def test_upgrade_preserves_all_legacy_rows_and_downgrade_does_not_rewrite_them(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = _migration_config(url)
    command.upgrade(config, "0009")
    engine = create_db_engine(url)
    factory = make_session_factory(engine)
    try:
        with factory.begin() as s:
            sync_tenants(s, load_tenants(EXAMPLE_TENANTS))
            seed_world(s)
            tenant = get_tenant(s)
            row = s.scalars(select(Identity).where(Identity.tenant_id == tenant.id)).first()
            assert row is not None
            row.account_type_override = "service"
            row.status = "missing"
            row.missing_since = NOW - timedelta(days=4)
            row.review_decision, row.reviewed_at = "keep", NOW
            s.add_all(
                AccountException(
                    tenant_id=tenant.id,
                    identity_id=row.id,
                    reason="Synthetische Ausnahme",
                    created_by="test",
                    created_at=NOW,
                    valid_until=NOW + timedelta(days=days),
                )
                for days in (-1, 10)
            )
            s.add(AuditLog(tenant_id=tenant.id, actor="test", action="identity.keep", at=NOW))
            # Eine bereits bereinigte Identität hat keinen beweisbaren Quellenverweis.
            s.add(Identity(tenant_id=tenant.id, key="legacy-without-raw", flagged=False))
        tables = sorted(set(inspect(engine).get_table_names()) - {"alembic_version"})
        before = _snapshot(engine, tables)

        run_migrations(url)
        run_migrations(url)  # wiederholter Start darf auch den neuen Stand nicht verändern
        assert _snapshot(engine, tables) == before
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010"
            )
        with factory.begin() as s:
            assert s.scalars(select(DirectorySource)).all() == []
            assert s.scalars(select(SourceAccount)).all() == []
            source = DirectorySource(
                tenant_id=tenant.id, source=Source.ENTRA, external_id=DIRECTORY_ID
            )
            s.add(source)
            s.flush()
            s.add(
                SourceAccount(
                    tenant_id=tenant.id, directory_source_id=source.id, external_id=OBJECT_ID
                )
            )

        command.downgrade(config, "0009")
        assert _snapshot(engine, tables) == before
        assert {"directory_source", "source_account"}.isdisjoint(inspect(engine).get_table_names())
        run_migrations(url)
        assert _snapshot(engine, tables) == before
    finally:
        engine.dispose()


def _source(s: Session, slug: str = "cg", source: Source = Source.ENTRA) -> DirectorySource:
    row = DirectorySource(tenant_id=get_tenant(s, slug).id, source=source, external_id=DIRECTORY_ID)
    s.add(row)
    s.flush()
    return row


def test_external_ids_are_scoped_by_tenant_directory_and_source(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        sources = [_source(s, slug, kind) for slug in ("cg", "cvh") for kind in Source]
        another = DirectorySource(
            tenant_id=sources[0].tenant_id,
            source=Source.ENTRA,
            external_id="00000000-0000-4000-8000-000000000003",
        )
        s.add(another)
        s.flush()
        sources.append(another)
        for source in sources:
            s.add(
                SourceAccount(
                    tenant_id=source.tenant_id, directory_source_id=source.id, external_id=OBJECT_ID
                )
            )
    with factory() as s:
        rows = s.scalars(
            select(SourceAccount).where(SourceAccount.tenant_id == sources[0].tenant_id)
        )
        assert len(list(rows)) == 3
        other = s.scalars(
            select(SourceAccount).where(SourceAccount.tenant_id == sources[2].tenant_id)
        )
        assert len(list(other)) == 2


@pytest.mark.parametrize("duplicate", ["directory", "account"])
def test_duplicate_external_reference_is_rejected(
    factory: sessionmaker[Session],
    duplicate: str,
) -> None:
    with factory.begin() as s:
        source = _source(s)
        s.add(
            SourceAccount(
                tenant_id=source.tenant_id, directory_source_id=source.id, external_id=OBJECT_ID
            )
        )
    row = (
        DirectorySource(tenant_id=source.tenant_id, source=Source.ENTRA, external_id=DIRECTORY_ID)
        if duplicate == "directory"
        else SourceAccount(
            tenant_id=source.tenant_id, directory_source_id=source.id, external_id=OBJECT_ID
        )
    )
    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(row)


@pytest.mark.parametrize("foreign_source", [True, False])
def test_source_account_cannot_reference_a_foreign_or_missing_directory(
    factory: sessionmaker[Session],
    foreign_source: bool,
) -> None:
    with factory.begin() as s:
        source = _source(s, "cvh")
        tenant_id = get_tenant(s).id
    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(
            SourceAccount(
                tenant_id=tenant_id,
                directory_source_id=source.id if foreign_source else source.id + 999,
                external_id=OBJECT_ID,
            )
        )


@pytest.mark.parametrize("table", ["directory", "account"])
@pytest.mark.parametrize("external_id", ["", " ", " Uppercase ", "UPPERCASE"])
def test_external_references_require_canonical_nonempty_ids(
    factory: sessionmaker[Session],
    table: str,
    external_id: str,
) -> None:
    with factory.begin() as s:
        source = _source(s)
    row = (
        DirectorySource(tenant_id=source.tenant_id, source=Source.AD, external_id=external_id)
        if table == "directory"
        else SourceAccount(
            tenant_id=source.tenant_id, directory_source_id=source.id, external_id=external_id
        )
    )
    with pytest.raises(IntegrityError), factory.begin() as s:
        s.add(row)


def test_raw_data_purge_leaves_source_references_and_tenant_deletion_is_scoped(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        seed_world(s)
        source = _source(s)
        other_source = _source(s, "cvh")
        s.add_all(
            SourceAccount(tenant_id=d.tenant_id, directory_source_id=d.id, external_id=OBJECT_ID)
            for d in (source, other_source)
        )
        tenant = get_tenant(s)
        make_run(s, tenant, Source.ENTRA)
        make_run(s, tenant, Source.AD)
    with factory.begin() as s:
        purged = purge_raw_data(s, 1, NOW + timedelta(days=2))
        assert (purged.entra_accounts, purged.ad_accounts, purged.skus) == (3, 1, 3)
        account = s.scalars(
            select(SourceAccount).where(SourceAccount.tenant_id == source.tenant_id)
        ).one()
        assert account.external_id == OBJECT_ID
        s.delete(get_tenant(s))
    with factory.begin() as s:
        assert s.scalars(select(DirectorySource)).one().id == other_source.id
        remaining = s.scalars(select(SourceAccount)).one()
        assert remaining.tenant_id == other_source.tenant_id
        # Löschen nur des Verzeichnisses kaskadiert ebenfalls ausschließlich auf seine Konten.
        s.execute(
            delete(DirectorySource).where(DirectorySource.tenant_id == other_source.tenant_id)
        )
    with factory() as s:
        assert s.scalars(select(SourceAccount)).all() == []
