from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import dns.exception
import dns.resolver
import pytest

from app.connectors.ad import (
    REASON_COUNT_DEVIATES,
    REASON_REFERENCE_EMPTY,
    REASON_REFERENCE_MISSING,
    WARN_DC_DEFECTIVE,
    WARN_DC_SUSPECT,
    WARN_DC_UNREACHABLE,
    WARN_MASTER_INCOMPLETE,
    DcStatus,
    NoHealthyDcError,
    collect_ad,
    discover_dcs,
    resolve_srv,
)
from app.connectors.ad_client import (
    ERR_CONNECT,
    ERR_NO_DC,
    ERR_NO_HEALTHY_DC,
    ERR_TLS,
    AdError,
    Directory,
)
from tests.ad_fakes import SERVICE_ACCOUNT, FakeDirectory, guid, opener_for, user

T1 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)
TS = datetime(2026, 9, 15, tzinfo=UTC)


def _dc(logons: dict[int, datetime] | None = None, **kw: object) -> FakeDirectory:
    users = [user(1, logon_ts=TS), user(2), user(3)]
    return FakeDirectory(users, logons, **kw)  # type: ignore[arg-type]


def _user_searches(directory: FakeDirectory) -> int:
    """Anzahl der Benutzer-Suchen (nicht der Referenzobjekt-Suchen) an einem DC."""
    return sum(1 for s in directory.searches if s.startswith("(&"))


def _report(result: object, host: str) -> object:
    return next(d for d in result.dcs if d.host == host)  # type: ignore[attr-defined]


def test_last_logon_is_maximum_over_all_dcs() -> None:
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": _dc({1: T1}),
        "dc2.example.invalid": _dc({1: T2, 2: T1}),
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)

    by_guid = {a.object_guid: a for a in result.accounts}
    assert by_guid[guid(1)].last_logon == T2
    assert by_guid[guid(1)].last_logon_per_dc == {
        "dc1.example.invalid": T1.isoformat(),
        "dc2.example.invalid": T2.isoformat(),
    }
    assert by_guid[guid(2)].last_logon == T1
    assert by_guid[guid(3)].last_logon is None  # nie angemeldet
    # lastLogonTimestamp bleibt getrennt (Phase 4 bildet das Gesamtmaximum)
    assert by_guid[guid(1)].last_logon_timestamp == TS
    assert result.master_host == "dc1.example.invalid"
    assert result.warnings == []
    assert [d.status for d in result.dcs] == [DcStatus.OK, DcStatus.OK]


def test_master_data_fields_are_parsed() -> None:
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": FakeDirectory([user(7, member_of=["CN=Gruppe,DC=x"])])
    }
    account = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT).accounts[0]
    assert account.sam_account_name == "user007"
    assert account.user_principal_name == "user007@beispiel.invalid"
    assert account.user_account_control == 512
    assert account.when_created == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    assert account.account_expires is None  # 0x7FFF… = läuft nie ab
    assert account.ou == "OU=Test,DC=beispiel,DC=invalid"
    assert account.distinguished_name is not None
    assert account.member_of == ["CN=Gruppe,DC=x"]
    assert account.master_data_incomplete is False


def test_unreachable_dc_is_reported_and_run_continues() -> None:
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": _dc({1: T1}),
        "dc2.example.invalid": AdError(ERR_CONNECT),
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    bad = _report(result, "dc2.example.invalid")
    assert (bad.status, bad.reason) == (DcStatus.UNREACHABLE, ERR_CONNECT)  # type: ignore[attr-defined]
    assert result.warnings == [WARN_DC_UNREACHABLE]
    assert len(result.accounts) == 3


def test_dc_with_empty_attributes_is_defective_and_not_used_for_master_data() -> None:
    """Muster LA-CG-GC: antwortet, liefert aber leere userAccountControl/whenCreated."""
    broken = FakeDirectory(
        [user(1, uac=None, created=None), user(2, uac=None, created=None)],
        refs={SERVICE_ACCOUNT: False, "krbtgt": False},
    )
    good = _dc({1: T1})
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": broken,
        "dc2.example.invalid": good,
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)

    bad = _report(result, "dc1.example.invalid")
    assert (bad.status, bad.reason) == (DcStatus.DEFECTIVE, REASON_REFERENCE_EMPTY)  # type: ignore[attr-defined]
    assert result.master_host == "dc2.example.invalid"
    assert all(a.user_account_control == 512 for a in result.accounts)
    assert result.warnings == [WARN_DC_DEFECTIVE]


def test_missing_reference_object_makes_dc_defective() -> None:
    no_krbtgt = _dc(refs={SERVICE_ACCOUNT: True})
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": no_krbtgt,
        "dc2.example.invalid": _dc(),
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    bad = _report(result, "dc1.example.invalid")
    assert bad.reason == REASON_REFERENCE_MISSING  # type: ignore[attr-defined]


def test_renamed_administrator_is_tolerated() -> None:
    """„Administrator“ darf fehlen (umbenannt); Dienstkonto und krbtgt nicht."""
    dc = _dc(refs={SERVICE_ACCOUNT: True, "krbtgt": True, "Administrator": None})
    dcs: dict[str, Directory | AdError] = {"dc1.example.invalid": dc}
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert result.dcs[0].status is DcStatus.OK


def test_empty_administrator_makes_dc_defective() -> None:
    dc = _dc(refs={SERVICE_ACCOUNT: True, "krbtgt": True, "Administrator": False})
    dcs: dict[str, Directory | AdError] = {"dc1.example.invalid": dc, "dc2.example.invalid": _dc()}
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert _report(result, "dc1.example.invalid").status is DcStatus.DEFECTIVE  # type: ignore[attr-defined]


def test_count_outlier_is_suspect_when_three_or_more_dcs() -> None:
    many = [user(n) for n in range(1, 101)]
    short = [user(n) for n in range(1, 91)]  # 10 % weniger Objekte
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": FakeDirectory(many),
        "dc2.example.invalid": FakeDirectory(many),
        "dc3.example.invalid": FakeDirectory(many),
        "dc4.example.invalid": FakeDirectory(short),
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    odd = _report(result, "dc4.example.invalid")
    assert (odd.status, odd.reason, odd.user_count) == (  # type: ignore[attr-defined]
        DcStatus.SUSPECT,
        REASON_COUNT_DEVIATES,
        90,
    )
    assert result.warnings == [WARN_DC_SUSPECT]


def test_count_check_needs_three_dcs_and_tolerates_one_percent() -> None:
    a = [user(n) for n in range(1, 101)]
    b = [user(n) for n in range(1, 100)]  # genau 1 %: noch kein Ausreißer
    three: dict[str, Directory | AdError] = {
        "dc1.example.invalid": FakeDirectory(a),
        "dc2.example.invalid": FakeDirectory(a),
        "dc3.example.invalid": FakeDirectory(b),
    }
    assert collect_ad(sorted(three), opener_for(three), SERVICE_ACCOUNT).warnings == []
    two: dict[str, Directory | AdError] = {
        "dc1.example.invalid": FakeDirectory(a),
        "dc2.example.invalid": FakeDirectory(a[:50]),
    }
    assert collect_ad(sorted(two), opener_for(two), SERVICE_ACCOUNT).warnings == []


def test_incomplete_master_data_is_filled_from_second_dc_and_flagged() -> None:
    first = FakeDirectory([user(1, uac=None, created=None), user(2)])
    second = FakeDirectory([user(1), user(2)])
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": first,
        "dc2.example.invalid": second,
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    by_guid = {a.object_guid: a for a in result.accounts}
    assert by_guid[guid(1)].user_account_control == 512
    assert by_guid[guid(1)].when_created == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    assert by_guid[guid(1)].master_data_incomplete is True
    assert by_guid[guid(2)].master_data_incomplete is False
    assert result.warnings == [WARN_MASTER_INCOMPLETE]


def test_master_data_is_read_only_once_more_for_filling() -> None:
    first = FakeDirectory([user(1, uac=None, created=None)])
    second, third = FakeDirectory([user(1)]), FakeDirectory([user(1)])
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": first,
        "dc2.example.invalid": second,
        "dc3.example.invalid": third,
    }
    collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert _user_searches(second) == 2  # lastLogon + Stammdaten zum Ergänzen
    assert _user_searches(third) == 1  # nur lastLogon


def test_all_connections_are_closed() -> None:
    dc = _dc()
    dcs: dict[str, Directory | AdError] = {"dc1.example.invalid": dc}
    collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert dc.closed


def test_no_healthy_dc_raises_with_reports() -> None:
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": AdError(ERR_TLS),
        "dc2.example.invalid": FakeDirectory([], refs={SERVICE_ACCOUNT: False}),
    }
    with pytest.raises(NoHealthyDcError) as caught:
        collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert caught.value.code == ERR_NO_HEALTHY_DC
    assert [r.status for r in caught.value.reports] == [DcStatus.UNREACHABLE, DcStatus.DEFECTIVE]
    assert str(caught.value) == f"AdError code={ERR_NO_HEALTHY_DC}"


def test_error_during_search_marks_dc_unreachable() -> None:
    class Dropping(FakeDirectory):
        def root_naming_context(self) -> str:
            raise AdError(ERR_CONNECT)

    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": Dropping([]),
        "dc2.example.invalid": _dc(),
    }
    result = collect_ad(sorted(dcs), opener_for(dcs), SERVICE_ACCOUNT)
    assert _report(result, "dc1.example.invalid").status is DcStatus.UNREACHABLE  # type: ignore[attr-defined]


def test_discover_prefers_configured_list_and_deduplicates() -> None:
    def never(_domain: str) -> list[str]:
        raise AssertionError("SRV darf nicht gefragt werden")

    hosts = discover_dcs(
        "example.invalid",
        ["DC2.example.invalid", "dc1.example.invalid", "dc2.example.invalid"],
        never,
    )
    assert hosts == ["dc1.example.invalid", "dc2.example.invalid"]


def test_discover_uses_srv_when_list_is_empty() -> None:
    assert discover_dcs("example.invalid", [], lambda _d: ["dc9.example.invalid"]) == [
        "dc9.example.invalid"
    ]
    with pytest.raises(AdError) as caught:
        discover_dcs("example.invalid", [], lambda _d: [])
    assert caught.value.code == ERR_NO_DC


def test_resolve_srv_returns_sorted_lowercase_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake_resolve(
        _self: object, qname: str, rdtype: str, lifetime: float
    ) -> list[SimpleNamespace]:
        asked.append(f"{qname} {rdtype}")
        return [
            SimpleNamespace(target="DC2.Example.invalid."),
            SimpleNamespace(target="dc1.example.invalid."),
        ]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", fake_resolve)
    assert resolve_srv("example.invalid") == ["dc1.example.invalid", "dc2.example.invalid"]
    assert asked == ["_ldap._tcp.dc._msdcs.example.invalid SRV"]


def test_resolve_srv_maps_dns_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*_a: object, **_k: object) -> None:
        raise dns.exception.Timeout

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", failing)
    with pytest.raises(AdError) as caught:
        resolve_srv("example.invalid")
    assert caught.value.code == ERR_NO_DC
