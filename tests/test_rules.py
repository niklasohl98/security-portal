from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Thresholds
from app.core import rules
from app.core.rules import AccountType, Status, Subject, ad_disabled, classify_account, evaluate

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
T = Thresholds()  # 30 / 90 / 14


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _subject(**kw: object) -> Subject:
    base = Subject(
        names=("max.muster",),
        in_entra=True,
        in_ad=False,
        created_at=_ago(400),
        entra_last=_ago(1),
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def _verdict(subject: Subject, patterns: dict[str, list[str]] | None = None) -> rules.Verdict:
    return evaluate(subject, T, patterns or {}, NOW)


def test_last_login_is_the_maximum_of_all_sources() -> None:
    s = _subject(entra_last=_ago(50), ad_last=_ago(5))
    assert _verdict(s).last_login == _ago(5)
    assert _verdict(_subject(entra_last=None, ad_last=None)).last_login is None


@pytest.mark.parametrize(
    ("days", "status", "flagged"),
    [
        (0, Status.ACTIVE, False),
        (30, Status.ACTIVE, False),  # genau 30 Tage: nicht „mehr als“
        (30.001, Status.INACTIVE, True),
        (90, Status.INACTIVE, True),
        (90.001, Status.INACTIVE_LONG, True),
    ],
)
def test_status_and_flag_by_age_of_last_login(days: float, status: Status, flagged: bool) -> None:
    v = _verdict(_subject(entra_last=_ago(days)))
    assert (v.status, v.flagged) == (status, flagged)


def test_thresholds_come_from_the_tenant_configuration() -> None:
    custom = Thresholds(inactive_days=10, long_inactive_days=20, new_account_grace_days=2)
    s = _subject(entra_last=_ago(15))
    assert evaluate(s, custom, {}, NOW).status is Status.INACTIVE
    assert evaluate(_subject(entra_last=_ago(25)), custom, {}, NOW).status is Status.INACTIVE_LONG


def test_never_signed_in_and_new_account() -> None:
    old = _verdict(_subject(entra_last=None, created_at=_ago(15)))
    assert (old.status, old.flagged) == (Status.NEVER, True)
    new = _verdict(_subject(entra_last=None, created_at=_ago(13)))
    assert (new.status, new.flagged) == (Status.NEW, False)
    # Ohne Anlagedatum lässt sich „neu“ nicht belegen.
    assert _verdict(_subject(entra_last=None, created_at=None)).status is Status.NEVER


def test_new_account_that_already_signed_in_is_not_new() -> None:
    assert _verdict(_subject(created_at=_ago(2), entra_last=_ago(1))).status is Status.ACTIVE


def test_disabled_accounts_count_but_have_their_own_status() -> None:
    v = _verdict(_subject(disabled=True, entra_last=_ago(200)))
    assert (v.status, v.flagged) == (Status.DISABLED, True)
    assert _verdict(_subject(disabled=True, entra_last=_ago(1))).flagged is False


def test_exception_wins_and_is_never_flagged() -> None:
    v = _verdict(_subject(has_exception=True, disabled=True, entra_last=None))
    assert (v.status, v.flagged, v.recommendation) == (Status.EXCEPTION, False, None)


def test_missing_sign_in_data_does_not_flag_entra_accounts() -> None:
    unknown = _verdict(_subject(sign_in_known=False, entra_last=None))
    assert (unknown.status, unknown.flagged, unknown.recommendation) == (
        Status.UNKNOWN,
        False,
        None,
    )
    # AD zeigt keine jüngste Anmeldung: Cloud-Nutzung ist nicht auszuschließen.
    stale_ad = _verdict(
        _subject(sign_in_known=False, entra_last=None, in_ad=True, ad_last=_ago(80))
    )
    assert stale_ad.status is Status.UNKNOWN
    # Eine jüngste AD-Anmeldung genügt als Beleg für „aktiv“.
    fresh_ad = _verdict(_subject(sign_in_known=False, entra_last=None, in_ad=True, ad_last=_ago(2)))
    assert fresh_ad.status is Status.ACTIVE
    # Reine AD-Konten sind davon nicht betroffen.
    ad_only = _verdict(_subject(sign_in_known=False, in_entra=False, in_ad=True, entra_last=None))
    assert ad_only.status is Status.NEVER


def test_ad_disabled_flag_bit() -> None:
    assert ad_disabled(0x202) is True
    assert ad_disabled(0x200) is False
    assert ad_disabled(None) is False


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        evaluate(_subject(), T, {}, datetime(2026, 11, 6, 12, 0))  # noqa: DTZ001 - absichtlich naiv


# --- Kontoart ---


@pytest.mark.parametrize(
    ("kw", "patterns", "expected"),
    [
        ({"is_guest": True, "names": ("svc-x",)}, {"service": ["svc*"]}, AccountType.GUEST),
        ({"names": ("max.adm",)}, {}, AccountType.ADMIN),  # Vorgabe: Name enthält adm
        ({"names": ("max.adm",)}, {"admin": []}, AccountType.MEMBER_CLOUD),  # Vorgabe abgeschaltet
        ({"names": ("Sync_App-01",)}, {"service": ["sync_*"]}, AccountType.SERVICE),
        (
            {"names": ("svc-adm",)},
            {"service": ["svc*"]},
            AccountType.ADMIN,
        ),  # Reihenfolge laut Plan
        ({"names": ("Test1",)}, {"test": ["test*", "demo*"]}, AccountType.TEST),
        ({"names": ("besucher",)}, {"resource": ["besucher", "wc"]}, AccountType.RESOURCE),
        ({"names": ("anna",), "synced": True}, {}, AccountType.MEMBER_SYNCED),
        ({"names": ("anna",)}, {}, AccountType.MEMBER_CLOUD),
        ({"names": ("anna",), "in_entra": False, "in_ad": True}, {}, AccountType.MEMBER_ONPREM),
    ],
)
def test_account_type(
    kw: dict[str, object], patterns: dict[str, list[str]], expected: AccountType
) -> None:
    assert classify_account(_subject(**kw), patterns) is expected


def test_manual_account_type_overrides_the_automatic_one() -> None:
    s = _subject(names=("test1",), entra_last=_ago(100))
    auto = evaluate(s, T, {"test": ["test*"]}, NOW)
    assert (auto.account_type, auto.recommendation) == (AccountType.TEST, rules.REC_DELETE)
    manual = evaluate(s, T, {"test": ["test*"]}, NOW, AccountType.SERVICE)
    assert (manual.account_type, manual.recommendation) == (AccountType.SERVICE, None)


# --- Empfehlung ---


def test_licensed_and_long_inactive_gets_revoke_then_disable() -> None:
    v = _verdict(_subject(licensed=True, entra_last=_ago(120)))
    assert v.recommendation == rules.REC_REVOKE_THEN_DISABLE
    assert _verdict(_subject(licensed=True, entra_last=_ago(60))).recommendation is None
    assert _verdict(_subject(licensed=False, entra_last=_ago(120))).recommendation is None


def test_licensed_and_disabled_only_needs_the_license_revoked() -> None:
    v = _verdict(_subject(licensed=True, disabled=True, entra_last=_ago(120)))
    assert (v.status, v.recommendation) == (Status.DISABLED, rules.REC_REVOKE)


def test_licensed_never_signed_in_counts_after_the_long_threshold() -> None:
    young = _verdict(_subject(licensed=True, entra_last=None, created_at=_ago(60)))
    assert young.recommendation is None
    old = _verdict(_subject(licensed=True, entra_last=None, created_at=_ago(120)))
    assert old.recommendation == rules.REC_REVOKE_THEN_DISABLE


def test_guest_never_signed_in_and_invited_long_ago_is_deleted() -> None:
    v = _verdict(_subject(is_guest=True, entra_last=None, created_at=_ago(120)))
    assert v.recommendation == rules.REC_DELETE_GUEST
    recent = _verdict(_subject(is_guest=True, entra_last=None, created_at=_ago(60)))
    assert recent.recommendation is None
    seen = _verdict(_subject(is_guest=True, entra_last=_ago(120), created_at=_ago(400)))
    assert seen.recommendation is None


def test_flagged_test_account_is_deleted_but_an_active_one_is_not() -> None:
    patterns = {"test": ["test*"]}
    assert _verdict(_subject(names=("test1",), entra_last=_ago(40)), patterns).recommendation == (
        rules.REC_DELETE
    )
    assert _verdict(_subject(names=("test1",), entra_last=_ago(2)), patterns).recommendation is None


def test_resource_mailbox_is_never_just_unlicensed() -> None:
    patterns = {"resource": ["wc"]}
    v = _verdict(_subject(names=("wc",), licensed=True, entra_last=None), patterns)
    assert v.recommendation == rules.REC_RESOURCE


def test_synced_account_without_ad_counterpart_is_orphaned_only_with_ad_data() -> None:
    assert _verdict(_subject(synced=True, ad_expected=True)).recommendation == rules.REC_ORPHANED
    assert _verdict(_subject(synced=True, ad_expected=False)).recommendation is None


def test_hint_when_only_one_side_is_used() -> None:
    both = {"in_ad": True, "synced": True, "ad_expected": True}
    local = _verdict(_subject(**both, entra_last=_ago(60), ad_last=_ago(1)))
    assert (local.status, local.recommendation) == (Status.ACTIVE, rules.REC_ONLY_ONPREM)
    cloud = _verdict(_subject(**both, entra_last=_ago(1), ad_last=None))
    assert cloud.recommendation == rules.REC_ONLY_CLOUD
    same = _verdict(_subject(**both, entra_last=_ago(1), ad_last=_ago(2)))
    assert same.recommendation is None
