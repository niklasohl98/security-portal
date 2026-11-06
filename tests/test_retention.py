from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.retention import purge_raw_data
from app.db.models import (
    AdAccount,
    EntraAccount,
    Identity,
    RunStatus,
    Source,
    SyncRun,
    TenantSku,
)
from tests.identity_fakes import NOW, ago, get_tenant, make_ad, make_entra, make_run


def _aged_run(s: Session, slug: str, source: Source, days: float, status: RunStatus) -> SyncRun:
    run = make_run(s, get_tenant(s, slug), source, status)
    run.started_at = ago(days)
    s.flush()
    return run


def test_old_raw_data_is_deleted_but_the_newest_usable_run_stays(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        old1 = _aged_run(s, "cg", Source.ENTRA, 200, RunStatus.SUCCESS)
        old2 = _aged_run(s, "cg", Source.ENTRA, 120, RunStatus.SUCCESS)
        newest_usable = _aged_run(s, "cg", Source.ENTRA, 100, RunStatus.PARTIAL)
        newer_failed = _aged_run(s, "cg", Source.ENTRA, 95, RunStatus.FAILED)
        for run, n in ((old1, 1), (old2, 2), (newest_usable, 3), (newer_failed, 4)):
            make_entra(s, run, n)
        s.add(TenantSku(tenant_id=t.id, sync_run_id=old1.id, sku_id="a", part_number="X"))
        s.add(Identity(tenant_id=t.id, key="k"))
    with factory.begin() as s:
        result = purge_raw_data(s, 90, NOW)
        assert (result.runs, result.entra_accounts, result.skus) == (3, 3, 1)
    with factory.begin() as s:
        kept = {a.display_name for a in s.scalars(select(EntraAccount))}
        assert kept == {"Person 3"}  # der jüngste brauchbare Lauf, auch wenn er alt ist
        assert len(s.scalars(select(SyncRun)).all()) == 4  # Protokoll bleibt


def test_recent_runs_and_other_tenants_are_untouched(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        recent = _aged_run(s, "cg", Source.ENTRA, 10, RunStatus.SUCCESS)
        make_entra(s, recent, 1)
        cvh_old = _aged_run(s, "cvh", Source.AD, 300, RunStatus.SUCCESS)
        make_ad(s, cvh_old, 1)  # einziger brauchbarer AD-Lauf von CVH: geschützt
    with factory.begin() as s:
        result = purge_raw_data(s, 90, NOW)
        assert result.runs == 0
        assert len(s.scalars(select(EntraAccount)).all()) == 1
        assert len(s.scalars(select(AdAccount)).all()) == 1


def test_each_source_keeps_its_own_newest_run(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        for source, n in ((Source.ENTRA, 1), (Source.AD, 2)):
            old = _aged_run(s, "cg", source, 200, RunStatus.SUCCESS)
            newest = _aged_run(s, "cg", source, 150, RunStatus.SUCCESS)
            (make_entra if source is Source.ENTRA else make_ad)(s, old, n)
            (make_entra if source is Source.ENTRA else make_ad)(s, newest, n + 10)
    with factory.begin() as s:
        assert purge_raw_data(s, 90, NOW).runs == 2
    with factory.begin() as s:
        assert len(s.scalars(select(EntraAccount)).all()) == 1
        assert len(s.scalars(select(AdAccount)).all()) == 1


def test_identity_references_become_null(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        old = _aged_run(s, "cg", Source.ENTRA, 200, RunStatus.SUCCESS)
        _aged_run(s, "cg", Source.ENTRA, 1, RunStatus.SUCCESS)
        acc = make_entra(s, old, 1)
        row = Identity(tenant_id=t.id, key="k", entra_account_id=acc.id)
        s.add(row)
        s.flush()
        row_id = row.id
    with factory.begin() as s:
        purge_raw_data(s, 90, NOW)
    with factory.begin() as s:
        loaded = s.get(Identity, row_id)
        assert loaded is not None
        assert loaded.entra_account_id is None


def test_retention_must_be_positive(factory: sessionmaker[Session]) -> None:
    with factory() as s, pytest.raises(ValueError, match="mindestens 1"):
        purge_raw_data(s, 0, NOW + timedelta(days=1))
