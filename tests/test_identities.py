from __future__ import annotations

import base64
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.identities import refresh_identities, run_identity_refresh
from app.core.sync import WARN_NO_SIGN_IN, SyncError
from app.db.models import (
    AccountException,
    Identity,
    RunStatus,
    SkuName,
    Source,
    TenantSku,
)
from tests.identity_fakes import (
    NOW,
    ago,
    by_name,
    get_tenant,
    lic,
    make_ad,
    make_entra,
    make_run,
)

GUID = "12345678-9abc-def0-1234-56789abcdef0"
SKU_PAID = "00000000-0000-4000-8000-0000000000a1"
SKU_FREE = "00000000-0000-4000-8000-0000000000a2"


def test_without_usable_runs_nothing_is_changed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        failed = make_run(s, t, Source.ENTRA, RunStatus.FAILED)
        make_entra(s, failed, 1, last_successful_sign_in=ago(1))
        s.add(Identity(tenant_id=t.id, key="alt", display_name="Alt"))
    with factory.begin() as s:
        result = refresh_identities(s, get_tenant(s), NOW)
        assert result.evaluated is False
        assert set(by_name(s, get_tenant(s))) == {"Alt"}


def test_merges_entra_and_ad_and_flags_by_the_latest_login_of_all_sources(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        er, ar = make_run(s, t, Source.ENTRA), make_run(s, t, Source.AD)
        raw = uuid.UUID(GUID).bytes_le
        make_entra(
            s,
            er,
            1,
            on_prem_sync_enabled=True,
            on_prem_immutable_id=base64.b64encode(raw).decode(),
            last_successful_sign_in=ago(60),
        )
        make_ad(s, ar, 1, object_guid=GUID, sam_account_name="x", last_logon=ago(3))  # AD jünger
        make_entra(s, er, 2, last_successful_sign_in=ago(45))
        make_entra(s, er, 3, last_non_interactive_sign_in=ago(2), last_successful_sign_in=ago(80))
        make_ad(s, ar, 4, sam_account_name="nur.ad", last_logon_timestamp=ago(100))

    with factory.begin() as s:
        t = get_tenant(s)
        result = refresh_identities(s, t, NOW)
        rows = by_name(s, t)

    assert (result.evaluated, result.identities, result.flagged) == (True, 4, 2)
    assert result.matches == {"immutable_id": 1}
    merged = rows["Person 1"]
    assert merged.entra_account_id
    assert merged.ad_account_id
    assert merged.last_login_at == ago(3)
    assert merged.flagged is False
    assert rows["Person 2"].status == "inactive"
    assert rows["Person 2"].flagged is True
    assert rows["Person 3"].last_login_at == ago(2)  # nicht interaktiv zählt
    ad_only = rows["nur.ad"]
    assert ad_only.entra_account_id is None
    assert (ad_only.status, ad_only.account_type) == ("inactive_long", "member_onprem")


def test_only_the_latest_usable_run_per_source_counts(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        old = make_run(s, t, Source.ENTRA)
        make_entra(s, old, 1, display_name="Alt", last_successful_sign_in=ago(1))
        new = make_run(s, t, Source.ENTRA, RunStatus.PARTIAL)
        make_entra(s, new, 2, display_name="Neu", last_successful_sign_in=ago(1))
        broken = make_run(s, t, Source.ENTRA, RunStatus.FAILED)
        make_entra(s, broken, 3, display_name="Kaputt")
    with factory.begin() as s:
        refresh_identities(s, get_tenant(s), NOW)
        assert set(by_name(s, get_tenant(s))) == {"Neu"}


def test_other_tenants_are_never_mixed_or_touched(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        cg, cvh = get_tenant(s, "cg"), get_tenant(s, "cvh")
        make_entra(s, make_run(s, cg, Source.ENTRA), 1, last_successful_sign_in=ago(1))
        make_entra(s, make_run(s, cvh, Source.ENTRA), 2, last_successful_sign_in=ago(1))
        s.add(Identity(tenant_id=cvh.id, key="fremd", display_name="CVH-Rest"))
    with factory.begin() as s:
        refresh_identities(s, get_tenant(s, "cg"), NOW)
        assert set(by_name(s, get_tenant(s, "cg"))) == {"Person 1"}
        assert set(by_name(s, get_tenant(s, "cvh"))) == {"CVH-Rest"}


def test_vanished_identity_is_marked_and_kept_not_deleted(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        make_entra(s, make_run(s, t, Source.ENTRA), 1, last_successful_sign_in=ago(1))
        s.add(
            Identity(tenant_id=t.id, key="weg", display_name="Weg", flagged=True, status="inactive")
        )
    with factory.begin() as s:
        result = refresh_identities(s, get_tenant(s), NOW)
        gone = by_name(s, get_tenant(s))["Weg"]
        assert (gone.status, gone.flagged, gone.recommendation) == ("missing", False, None)
        assert gone.missing_since == NOW
        assert (result.identities, result.missing) == (1, 1)
    with factory.begin() as s:  # später noch einmal: Markierung und Zeitpunkt bleiben
        refresh_identities(s, get_tenant(s), NOW + timedelta(days=3))
        rows = by_name(s, get_tenant(s))
        assert set(rows) == {"Person 1", "Weg"}
        assert rows["Weg"].missing_since == NOW
        assert rows["Person 1"].missing_since is None


def test_only_undecided_vanished_identities_count_as_missing(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        make_entra(s, make_run(s, t, Source.ENTRA), 1, last_successful_sign_in=ago(1))
        s.add(Identity(tenant_id=t.id, key="a", display_name="A"))
        s.add(
            Identity(
                tenant_id=t.id,
                key="b",
                display_name="B",
                missing_since=ago(5),
                review_decision="keep",
                reviewed_at=ago(4),
            )
        )
    with factory.begin() as s:
        result = refresh_identities(s, get_tenant(s), NOW)
        rows = by_name(s, get_tenant(s))
        assert result.missing == 1  # nur A wartet auf eine Entscheidung
        assert rows["B"].review_decision == "keep"
        assert rows["B"].missing_since == ago(5)  # Zeitpunkt bleibt


def test_returning_account_clears_the_mark_and_the_decision(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        run = make_run(s, t, Source.ENTRA)
        acc = make_entra(s, run, 1, last_successful_sign_in=ago(1))
        s.add(
            Identity(
                tenant_id=t.id,
                key=acc.object_id,
                display_name="Person 1",
                status="missing",
                missing_since=ago(9),
                review_decision="keep",
                reviewed_at=ago(8),
            )
        )
    with factory.begin() as s:
        result = refresh_identities(s, get_tenant(s), NOW)
        row = by_name(s, get_tenant(s))["Person 1"]
        assert (row.missing_since, row.review_decision, row.reviewed_at) == (None, None, None)
        assert (row.status, result.missing) == ("active", 0)


def test_vanished_identity_keeps_its_exceptions(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        make_entra(s, make_run(s, t, Source.ENTRA), 1, last_successful_sign_in=ago(1))
        gone = Identity(tenant_id=t.id, key="weg", display_name="Weg")
        s.add(gone)
        s.flush()
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=gone.id,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=30),
            )
        )
    with factory.begin() as s:
        refresh_identities(s, get_tenant(s), NOW)
        assert len(s.scalars(select(AccountException)).all()) == 1


def test_valid_exception_is_honoured_and_an_expired_one_is_not(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        run = make_run(s, t, Source.ENTRA)
        for n in (1, 2):
            make_entra(s, run, n, last_successful_sign_in=ago(200))
    with factory.begin() as s:
        t = get_tenant(s)
        refresh_identities(s, t, NOW)
        rows = by_name(s, t)
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
        t = get_tenant(s)
        refresh_identities(s, t, NOW)
        rows = by_name(s, t)
        assert (rows["Person 1"].status, rows["Person 1"].flagged) == ("exception", False)
        assert (rows["Person 2"].status, rows["Person 2"].flagged) == ("inactive_long", True)


def test_identity_keeps_exception_and_override_when_an_ad_account_gets_an_entra_partner(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        ar = make_run(s, t, Source.AD)
        make_ad(s, ar, 1, sam_account_name="meier", last_logon=ago(100))
    with factory.begin() as s:
        t = get_tenant(s)
        refresh_identities(s, t, NOW)
        row = by_name(s, t)["meier"]
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
        t = get_tenant(s)
        er = make_run(s, t, Source.ENTRA)
        make_entra(s, er, 7, on_prem_sam_account_name="meier", last_successful_sign_in=ago(100))
    with factory.begin() as s:
        t = get_tenant(s)
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
        t = get_tenant(s)
        er, ar = make_run(s, t, Source.ENTRA), make_run(s, t, Source.AD)
        e = make_entra(s, er, 1, on_prem_sam_account_name="meier", last_successful_sign_in=ago(100))
        a = make_ad(s, ar, 1, sam_account_name="meier", last_logon=ago(100))
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
        t = get_tenant(s)
        refresh_identities(s, t, NOW)
        rows = s.scalars(select(Identity).where(Identity.tenant_id == t.id)).all()
        assert len(rows) == 1
        assert rows[0].status == "exception"
        assert len(s.scalars(select(AccountException)).all()) == 1


def test_missing_sign_in_data_flags_nobody_from_entra(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        run = make_run(s, t, Source.ENTRA, RunStatus.PARTIAL, [WARN_NO_SIGN_IN])
        make_entra(s, run, 1)  # alle Anmeldewerte leer, weil Graph sie nicht lieferte
    with factory.begin() as s:
        result = refresh_identities(s, get_tenant(s), NOW)
        row = by_name(s, get_tenant(s))["Person 1"]
        assert (row.status, row.flagged, result.flagged) == ("unknown", False, 0)


def test_free_licences_do_not_count_as_licensed(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        run = make_run(s, t, Source.ENTRA)
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
        make_entra(s, run, 1, last_successful_sign_in=ago(200), licenses=[lic(SKU_FREE)])
        make_entra(
            s, run, 2, last_successful_sign_in=ago(200), licenses=[lic(SKU_FREE), lic(SKU_PAID)]
        )
    with factory.begin() as s:
        refresh_identities(s, get_tenant(s), NOW)
        rows = by_name(s, get_tenant(s))
        assert rows["Person 1"].recommendation is None
        assert rows["Person 2"].recommendation == "Lizenz entziehen, dann deaktivieren"


def test_tenant_patterns_drive_the_account_type(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        t.account_patterns = {"service": ["svc*"]}
        make_entra(
            s,
            make_run(s, t, Source.ENTRA),
            1,
            user_principal_name="svc-backup@beispiel.test",
            last_successful_sign_in=ago(1),
        )
    with factory.begin() as s:
        refresh_identities(s, get_tenant(s), NOW)
        assert by_name(s, get_tenant(s))["Person 1"].account_type == "service"


def test_run_identity_refresh_commits_and_rejects_unknown_tenant(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        make_entra(s, make_run(s, get_tenant(s), Source.ENTRA), 1, last_successful_sign_in=ago(1))
    result = run_identity_refresh(factory, "cg")
    assert (result.identities, result.flagged) == (1, 0)
    with factory() as s:
        assert len(s.scalars(select(Identity)).all()) == 1
    with pytest.raises(SyncError):
        run_identity_refresh(factory, "gibt-es-nicht")
