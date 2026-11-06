from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.core.accounts import AccountFilter, apply_filter, get_account, load_accounts
from app.core.overview import list_runs, tenant_overview
from app.db.models import (
    AccountException,
    Identity,
    RunStatus,
    Source,
)
from tests.identity_fakes import NOW, ago, get_tenant, make_run, seed_world


def test_load_marks_licenses_names_source_and_days(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
    with factory() as s:
        rows = {r.display_name: r for r in load_accounts(s, get_tenant(s), NOW)}
        one = rows["Person 1"]
        assert [(lic.name, lic.free) for lic in one.licenses] == [
            ("Microsoft 365 E3", False),  # bezahlte vor kostenlosen
            ("Flow Free", True),
        ]
        assert one.licensed is True
        assert [lic.name for lic in one.paid_licenses] == ["Microsoft 365 E3"]
        assert (one.source, one.days_since_login) == ("entra", 200)
        assert rows["Person 2"].licenses[0].name == "NEU_X"  # unbekannt: Teilenummer
        assert rows["Person 2"].days_since_login is None
        assert rows["Person 3"].licensed is False
        assert rows["nur.ad"].source == "ad"
        assert rows["nur.ad"].sam_account_name == "nur.ad"


def test_missing_identities_and_other_tenants_are_not_listed(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        seed_world(s)
        s.add(
            Identity(
                tenant_id=get_tenant(s).id, key="weg", display_name="Weg", missing_since=ago(3)
            )
        )
        s.add(Identity(tenant_id=get_tenant(s, "cvh").id, key="x", display_name="Fremd"))
    with factory() as s:
        names = {r.display_name for r in load_accounts(s, get_tenant(s), NOW)}
        assert names == {"Person 1", "Person 2", "Person 3", "nur.ad"}
        assert {r.display_name for r in load_accounts(s, get_tenant(s, "cvh"), NOW)} == {"Fremd"}


def test_get_account_is_scoped_to_the_tenant(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
        foreign = Identity(tenant_id=get_tenant(s, "cvh").id, key="x", display_name="Fremd")
        s.add(foreign)
        s.flush()
        foreign_id = foreign.id
    with factory() as s:
        cg = get_tenant(s)
        mine = next(r for r in load_accounts(s, cg, NOW) if r.display_name == "Person 1")
        assert get_account(s, cg, mine.identity_id, NOW) is not None
        assert get_account(s, cg, foreign_id, NOW) is None


def test_default_filter_shows_flagged_only_sorted_by_days_never_first(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        seed_world(s)
    with factory() as s:
        rows = load_accounts(s, get_tenant(s), NOW)
        got = [r.display_name for r in apply_filter(rows, AccountFilter())]
        assert got == ["Person 2", "Person 1", "nur.ad"]  # nie, 200 Tage, 50 Tage
        everyone = apply_filter(rows, AccountFilter(flagged_only=False))
        assert [r.display_name for r in everyone][-1] == "Person 3"  # jüngste Anmeldung zuletzt


def test_filters_combine(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
    with factory() as s:
        rows = load_accounts(s, get_tenant(s), NOW)

        def names(**kw: object) -> list[str | None]:
            return [r.display_name for r in apply_filter(rows, AccountFilter(**kw))]  # type: ignore[arg-type]

        assert names(status="inactive_long") == ["Person 1"]
        assert names(licensed=True) == ["Person 2", "Person 1"]
        assert names(licensed=False) == ["nur.ad"]
        assert names(source="ad") == ["nur.ad"]
        assert names(account_type="member_onprem") == ["nur.ad"]
        assert names(query="  PERSON 1 ") == ["Person 1"]
        assert names(query="NUR.AD") == ["nur.ad"]
        assert names(query="gibt-es-nicht") == []
        assert names(flagged_only=False, query="user3@") == ["Person 3"]  # UPN-Suche


def test_exception_until_is_shown_only_while_valid(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
        t = get_tenant(s)
        ident = {r.display_name: r.identity_id for r in load_accounts(s, t, NOW)}
        for name, days in (("Person 1", 30), ("Person 2", -5)):
            s.add(
                AccountException(
                    tenant_id=t.id,
                    identity_id=ident[name],
                    reason="x",
                    created_by="t",
                    valid_until=NOW + timedelta(days=days),
                )
            )
    with factory() as s:
        rows = {r.display_name: r for r in load_accounts(s, get_tenant(s), NOW)}
        assert rows["Person 1"].exception_until == NOW + timedelta(days=30)
        assert rows["Person 2"].exception_until is None


def test_overview_counts_and_releasing_licenses(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
        t = get_tenant(s)
        s.add(Identity(tenant_id=t.id, key="a", display_name="A", missing_since=ago(1)))
        s.add(
            Identity(
                tenant_id=t.id,
                key="b",
                display_name="B",
                missing_since=ago(1),
                review_decision="keep",
            )
        )
        ident = next(
            r.identity_id for r in load_accounts(s, t, NOW) if r.display_name == "Person 3"
        )
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=ident,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=10),
            )
        )
        s.add(
            AccountException(
                tenant_id=t.id,
                identity_id=ident,
                reason="y",
                created_by="t",
                valid_until=NOW - timedelta(days=1),
            )
        )
    with factory() as s:
        t = get_tenant(s)
        ov = tenant_overview(s, t, load_accounts(s, t, NOW), NOW)
        assert (ov.total, ov.flagged, ov.flagged_licensed) == (4, 3, 2)
        assert ov.releasing_licenses == {"NEU_X": 1, "Microsoft 365 E3": 1}
        assert ov.by_status == {"inactive_long": 1, "never": 1, "active": 1, "disabled": 1}
        assert ov.missing_open == 1  # B ist entschieden
        assert (ov.exceptions_expiring, ov.exceptions_expired) == (1, 1)
        assert [r.source for r in ov.latest_runs] == [Source.ENTRA, Source.AD]
        assert ov.warnings == []


def test_overview_collects_warnings_from_the_latest_runs_only(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        old = make_run(s, t, Source.ENTRA, RunStatus.PARTIAL, ["sign_in_activity_unavailable"])
        old.account_count = 5
        make_run(s, t, Source.AD, RunStatus.PARTIAL, ["ad_dc_defective", "ad_dc_unreachable"])
        make_run(s, t, Source.ENTRA, RunStatus.FAILED)  # jüngster Entra-Lauf ersetzt den alten
    with factory() as s:
        t = get_tenant(s)
        ov = tenant_overview(s, t, [], NOW)
        assert ov.warnings == [
            "run_failed",
            "ad_dc_defective",
            "ad_dc_unreachable",
        ]  # Entra, dann AD


def test_list_runs_is_newest_first_limited_and_scoped(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        cg, cvh = get_tenant(s), get_tenant(s, "cvh")
        for i in range(3):
            run = make_run(s, cg, Source.ENTRA)
            run.started_at = ago(10 - i)
            run.finished_at = run.started_at + timedelta(seconds=42)
        make_run(s, cvh, Source.AD)
    with factory() as s:
        cg = get_tenant(s)
        runs = list_runs(s, [cg], limit=2)
        assert len(runs) == 2
        assert runs[0].started_at > runs[1].started_at
        assert runs[0].duration_seconds == 42
        assert all(r.tenant_slug == "cg" for r in runs)
        assert list_runs(s, []) == []
