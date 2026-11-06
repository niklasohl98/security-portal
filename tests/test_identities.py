from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, load_tenants
from app.core.identities import refresh_identities, run_identity_refresh
from app.core.sync import WARN_NO_SIGN_IN, SyncError
from app.core.tenants import sync_tenants
from app.db.models import (
    AccountException,
    AdAccount,
    EntraAccount,
    Identity,
    RunStatus,
    SkuName,
    Source,
    SyncRun,
    Tenant,
    TenantSku,
)
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.conftest import EXAMPLE_TENANTS

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
GUID = "12345678-9abc-def0-1234-56789abcdef0"
SKU_PAID = "00000000-0000-4000-8000-0000000000a1"
SKU_FREE = "00000000-0000-4000-8000-0000000000a2"


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    f = make_session_factory(engine)
    with f.begin() as s:
        sync_tenants(s, load_tenants(EXAMPLE_TENANTS))
    yield f
    engine.dispose()


def _tenant(s: Session, slug: str = "cg") -> Tenant:
    return s.scalars(select(Tenant).where(Tenant.slug == slug)).one()


def _run(
    s: Session,
    tenant: Tenant,
    source: Source,
    status: RunStatus = RunStatus.SUCCESS,
    warnings: list[str] | None = None,
) -> SyncRun:
    run = SyncRun(
        tenant_id=tenant.id,
        source=source,
        status=status,
        started_at=NOW,
        warnings=warnings or [],
    )
    s.add(run)
    s.flush()
    return run


def _entra(s: Session, run: SyncRun, n: int, **kw: Any) -> EntraAccount:
    kw.setdefault("user_principal_name", f"user{n}@beispiel.test")
    kw.setdefault("display_name", f"Person {n}")
    kw.setdefault("created_at", _ago(400))
    acc = EntraAccount(
        tenant_id=run.tenant_id, sync_run_id=run.id, object_id=f"e{n:07d}-0000-4000-8000-0", **kw
    )
    s.add(acc)
    s.flush()
    return acc


def _ad(s: Session, run: SyncRun, n: int, **kw: Any) -> AdAccount:
    kw.setdefault("object_guid", f"{n:08d}-0000-4000-8000-0")
    acc = AdAccount(tenant_id=run.tenant_id, sync_run_id=run.id, **kw)
    s.add(acc)
    s.flush()
    return acc


def lic(sku: str) -> dict[str, Any]:
    return {"sku_id": sku, "direct": True, "group": False, "state": "Active"}


def _identities(s: Session, tenant: Tenant) -> dict[str, Identity]:
    rows = s.scalars(select(Identity).where(Identity.tenant_id == tenant.id))
    return {i.display_name or "": i for i in rows}


def test_without_usable_runs_nothing_is_changed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        failed = _run(s, t, Source.ENTRA, RunStatus.FAILED)
        _entra(s, failed, 1, last_successful_sign_in=_ago(1))
        s.add(Identity(tenant_id=t.id, key="alt", display_name="Alt"))
    with factory.begin() as s:
        result = refresh_identities(s, _tenant(s), NOW)
        assert result.evaluated is False
        assert set(_identities(s, _tenant(s))) == {"Alt"}


def test_merges_entra_and_ad_and_flags_by_the_latest_login_of_all_sources(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        er, ar = _run(s, t, Source.ENTRA), _run(s, t, Source.AD)
        raw = uuid.UUID(GUID).bytes_le
        _entra(
            s,
            er,
            1,
            on_prem_sync_enabled=True,
            on_prem_immutable_id=base64.b64encode(raw).decode(),
            last_successful_sign_in=_ago(60),
        )
        _ad(s, ar, 1, object_guid=GUID, sam_account_name="x", last_logon=_ago(3))  # AD jünger
        _entra(s, er, 2, last_successful_sign_in=_ago(45))
        _entra(s, er, 3, last_non_interactive_sign_in=_ago(2), last_successful_sign_in=_ago(80))
        _ad(s, ar, 4, sam_account_name="nur.ad", last_logon_timestamp=_ago(100))

    with factory.begin() as s:
        t = _tenant(s)
        result = refresh_identities(s, t, NOW)
        rows = _identities(s, t)

    assert (result.evaluated, result.identities, result.flagged) == (True, 4, 2)
    assert result.matches == {"immutable_id": 1}
    merged = rows["Person 1"]
    assert merged.entra_account_id
    assert merged.ad_account_id
    assert merged.last_login_at == _ago(3)
    assert merged.flagged is False
    assert rows["Person 2"].status == "inactive"
    assert rows["Person 2"].flagged is True
    assert rows["Person 3"].last_login_at == _ago(2)  # nicht interaktiv zählt
    ad_only = rows["nur.ad"]
    assert ad_only.entra_account_id is None
    assert (ad_only.status, ad_only.account_type) == ("inactive_long", "member_onprem")


def test_only_the_latest_usable_run_per_source_counts(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        old = _run(s, t, Source.ENTRA)
        _entra(s, old, 1, display_name="Alt", last_successful_sign_in=_ago(1))
        new = _run(s, t, Source.ENTRA, RunStatus.PARTIAL)
        _entra(s, new, 2, display_name="Neu", last_successful_sign_in=_ago(1))
        broken = _run(s, t, Source.ENTRA, RunStatus.FAILED)
        _entra(s, broken, 3, display_name="Kaputt")
    with factory.begin() as s:
        refresh_identities(s, _tenant(s), NOW)
        assert set(_identities(s, _tenant(s))) == {"Neu"}


def test_other_tenants_are_never_mixed_or_touched(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        cg, cvh = _tenant(s, "cg"), _tenant(s, "cvh")
        _entra(s, _run(s, cg, Source.ENTRA), 1, last_successful_sign_in=_ago(1))
        _entra(s, _run(s, cvh, Source.ENTRA), 2, last_successful_sign_in=_ago(1))
        s.add(Identity(tenant_id=cvh.id, key="fremd", display_name="CVH-Rest"))
    with factory.begin() as s:
        refresh_identities(s, _tenant(s, "cg"), NOW)
        assert set(_identities(s, _tenant(s, "cg"))) == {"Person 1"}
        assert set(_identities(s, _tenant(s, "cvh"))) == {"CVH-Rest"}


def test_is_idempotent_and_removes_identities_without_an_account(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        _entra(s, _run(s, t, Source.ENTRA), 1, last_successful_sign_in=_ago(1))
        s.add(Identity(tenant_id=t.id, key="weg", display_name="Weg"))
    for _ in range(2):
        with factory.begin() as s:
            refresh_identities(s, _tenant(s), NOW)
            assert set(_identities(s, _tenant(s))) == {"Person 1"}
        with factory.begin() as s:
            assert len(s.scalars(select(Identity)).all()) == 1


def test_valid_exception_is_honoured_and_an_expired_one_is_not(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        run = _run(s, t, Source.ENTRA)
        for n in (1, 2):
            _entra(s, run, n, last_successful_sign_in=_ago(200))
    with factory.begin() as s:
        t = _tenant(s)
        refresh_identities(s, t, NOW)
        rows = _identities(s, t)
        for name, until in (
            ("Person 1", NOW + timedelta(days=5)),
            ("Person 2", NOW - timedelta(days=1)),
        ):
            s.add(
                AccountException(
                    tenant_id=t.id,
                    identity_id=rows[name].id,
                    reason="Break-Glass",
                    created_by="test",
                    valid_until=until,
                )
            )
    with factory.begin() as s:
        t = _tenant(s)
        refresh_identities(s, t, NOW)
        rows = _identities(s, t)
        assert (rows["Person 1"].status, rows["Person 1"].flagged) == ("exception", False)
        assert (rows["Person 2"].status, rows["Person 2"].flagged) == ("inactive_long", True)


def test_identity_keeps_exception_and_override_when_an_ad_account_gets_an_entra_partner(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        ar = _run(s, t, Source.AD)
        _ad(s, ar, 1, sam_account_name="meier", last_logon=_ago(100))
    with factory.begin() as s:
        t = _tenant(s)
        refresh_identities(s, t, NOW)
        row = _identities(s, t)["meier"]
        row.account_type_override = "service"
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=row.id,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=9),
            )
        )
        row_id = row.id
    with factory.begin() as s:  # später kommt ein Entra-Lauf mit Partner (gleicher sAMAccountName)
        t = _tenant(s)
        er = _run(s, t, Source.ENTRA)
        _entra(s, er, 7, on_prem_sam_account_name="meier", last_successful_sign_in=_ago(100))
    with factory.begin() as s:
        t = _tenant(s)
        refresh_identities(s, t, NOW)
        rows = s.scalars(select(Identity).where(Identity.tenant_id == t.id)).all()
        assert [r.id for r in rows] == [row_id]
        assert rows[0].key.startswith("e0000007")  # auf die Entra-Objekt-ID umgestellt
        assert rows[0].account_type_override == "service"
        assert rows[0].status == "exception"


def test_two_old_rows_that_merge_into_one_hand_over_their_exceptions(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        er, ar = _run(s, t, Source.ENTRA), _run(s, t, Source.AD)
        e = _entra(s, er, 1, on_prem_sam_account_name="meier", last_successful_sign_in=_ago(100))
        a = _ad(s, ar, 1, sam_account_name="meier", last_logon=_ago(100))
        only_ad = Identity(tenant_id=t.id, key=a.object_guid, display_name="meier")
        only_entra = Identity(tenant_id=t.id, key=e.object_id, display_name="Person 1")
        s.add_all([only_ad, only_entra])
        s.flush()
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=only_ad.id,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=9),
            )
        )
    with factory.begin() as s:
        t = _tenant(s)
        refresh_identities(s, t, NOW)
        rows = s.scalars(select(Identity).where(Identity.tenant_id == t.id)).all()
        assert len(rows) == 1
        assert rows[0].status == "exception"
        assert len(s.scalars(select(AccountException)).all()) == 1


def test_missing_sign_in_data_flags_nobody_from_entra(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        run = _run(s, t, Source.ENTRA, RunStatus.PARTIAL, [WARN_NO_SIGN_IN])
        _entra(s, run, 1)  # alle Anmeldewerte leer, weil Graph sie nicht lieferte
    with factory.begin() as s:
        result = refresh_identities(s, _tenant(s), NOW)
        row = _identities(s, _tenant(s))["Person 1"]
        assert (row.status, row.flagged, result.flagged) == ("unknown", False, 0)


def test_free_licences_do_not_count_as_licensed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        run = _run(s, t, Source.ENTRA)
        s.add_all(
            [
                SkuName(part_number="FREE_PLAN", display_name="Frei", is_free=True),
                SkuName(part_number="PAID_PLAN", display_name="Bezahlt", is_free=False),
                TenantSku(
                    tenant_id=t.id, sync_run_id=run.id, sku_id=SKU_FREE, part_number="FREE_PLAN"
                ),
                TenantSku(
                    tenant_id=t.id, sync_run_id=run.id, sku_id=SKU_PAID, part_number="PAID_PLAN"
                ),
            ]
        )
        _entra(s, run, 1, last_successful_sign_in=_ago(200), licenses=[lic(SKU_FREE)])
        _entra(
            s, run, 2, last_successful_sign_in=_ago(200), licenses=[lic(SKU_FREE), lic(SKU_PAID)]
        )
    with factory.begin() as s:
        refresh_identities(s, _tenant(s), NOW)
        rows = _identities(s, _tenant(s))
        assert rows["Person 1"].recommendation is None
        assert rows["Person 2"].recommendation == "Lizenz entziehen, dann deaktivieren"


def test_tenant_patterns_drive_the_account_type(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = _tenant(s)
        t.account_patterns = {"service": ["svc*"]}
        _entra(
            s,
            _run(s, t, Source.ENTRA),
            1,
            user_principal_name="svc-backup@beispiel.test",
            last_successful_sign_in=_ago(1),
        )
    with factory.begin() as s:
        refresh_identities(s, _tenant(s), NOW)
        assert _identities(s, _tenant(s))["Person 1"].account_type == "service"


def test_run_identity_refresh_commits_and_rejects_unknown_tenant(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        _entra(s, _run(s, _tenant(s), Source.ENTRA), 1, last_successful_sign_in=_ago(1))
    result = run_identity_refresh(factory, "cg")
    assert (result.identities, result.flagged) == (1, 0)
    with factory() as s:
        assert len(s.scalars(select(Identity)).all()) == 1
    with pytest.raises(SyncError):
        run_identity_refresh(factory, "gibt-es-nicht")
