from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import (
    ExceptionError,
    create_exception,
    default_valid_until,
    list_exceptions,
    revoke_exception,
)
from app.db.models import AccountException, AuditLog, Identity, Source
from tests.identity_fakes import NOW, ago, by_name, get_tenant, make_entra, make_run

ACTOR = "test-akteur"


def _inactive_identity(s: Session, slug: str = "cg") -> int:
    """Eine seit 200 Tagen inaktive Identität (nach Auswertung geflaggt)."""
    from app.core.identities import refresh_identities

    t = get_tenant(s, slug)
    make_entra(s, make_run(s, t, Source.ENTRA), 1, last_successful_sign_in=ago(200))
    refresh_identities(s, t, NOW)
    return by_name(s, t)["Person 1"].id


def test_default_validity_comes_from_the_tenant(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        t = get_tenant(s)
        assert default_valid_until(t, NOW) == NOW + timedelta(days=365)
        t.exception_days = 30
        assert default_valid_until(t, NOW) == NOW + timedelta(days=30)


def test_exception_unflags_the_identity_immediately_and_is_audited(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s)
        assert by_name(s, get_tenant(s))["Person 1"].flagged is True
        create_exception(
            s, get_tenant(s), identity_id, "  Break-Glass  ", NOW + timedelta(days=100), ACTOR, NOW
        )
    with factory() as s:
        row = by_name(s, get_tenant(s))["Person 1"]
        assert (row.status, row.flagged) == ("exception", False)
        (ex,) = s.scalars(select(AccountException)).all()
        assert (ex.reason, ex.created_by, ex.created_at) == ("Break-Glass", ACTOR, NOW)
        (entry,) = s.scalars(select(AuditLog)).all()
        assert entry.action == "exception.create"
        assert (
            entry.detail == f"exception_id={ex.id} identity_id={identity_id} gueltig_bis=2027-02-14"
        )
        assert "Break-Glass" not in (entry.detail or "")  # Grund steht nicht im Audit-Log


@pytest.mark.parametrize(
    ("reason", "days", "code"),
    [
        ("", 10, "reason_required"),
        ("   ", 10, "reason_required"),
        ("x" * 501, 10, "reason_too_long"),
        ("ok", 0, "not_in_future"),
        ("ok", -1, "not_in_future"),
    ],
)
def test_invalid_exceptions_are_refused(
    factory: sessionmaker[Session], reason: str, days: int, code: str
) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s)
    with factory() as s, pytest.raises(ExceptionError) as err:
        create_exception(
            s, get_tenant(s), identity_id, reason, NOW + timedelta(days=days), ACTOR, NOW
        )
    assert err.value.code == code
    with factory() as s:
        assert s.scalars(select(AccountException)).all() == []


def test_naive_valid_until_is_rejected(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s)
    with factory() as s, pytest.raises(ValueError, match="timezone"):
        create_exception(s, get_tenant(s), identity_id, "x", NOW.replace(tzinfo=None), ACTOR, NOW)


def test_only_one_valid_exception_per_identity_but_an_expired_one_can_be_renewed(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s)
        create_exception(s, get_tenant(s), identity_id, "a", NOW + timedelta(days=10), ACTOR, NOW)
    with factory() as s, pytest.raises(ExceptionError) as err:
        create_exception(s, get_tenant(s), identity_id, "b", NOW + timedelta(days=20), ACTOR, NOW)
    assert err.value.code == "already_valid"
    later = NOW + timedelta(days=11)  # die erste ist abgelaufen
    with factory.begin() as s:
        create_exception(
            s, get_tenant(s), identity_id, "b", later + timedelta(days=5), ACTOR, later
        )
    with factory() as s:
        assert len(s.scalars(select(AccountException)).all()) == 2


def test_exceptions_cannot_cross_tenants(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s, "cvh")
    with factory() as s, pytest.raises(ExceptionError) as err:
        create_exception(
            s, get_tenant(s, "cg"), identity_id, "x", NOW + timedelta(days=5), ACTOR, NOW
        )
    assert err.value.code == "not_found"
    with factory() as s, pytest.raises(ExceptionError) as err:
        revoke_exception(s, get_tenant(s, "cg"), 999, ACTOR, NOW)
    assert err.value.code == "not_found"


def test_revoke_flags_the_identity_again_and_is_audited(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s)
        ex = create_exception(
            s, get_tenant(s), identity_id, "x", NOW + timedelta(days=50), ACTOR, NOW
        )
        ex_id = ex.id
    with factory.begin() as s:
        revoke_exception(s, get_tenant(s), ex_id, ACTOR, NOW)
    with factory() as s:
        assert by_name(s, get_tenant(s))["Person 1"].flagged is True
        assert s.scalars(select(AccountException)).all() == []
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert actions == ["exception.create", "exception.delete"]


def test_list_marks_expired_and_soon_expiring_exceptions(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        ids = []
        run = make_run(s, t, Source.ENTRA)
        for n in (1, 2, 3):
            make_entra(s, run, n, last_successful_sign_in=ago(1))
        from app.core.identities import refresh_identities

        refresh_identities(s, t, NOW)
        rows = by_name(s, t)
        for name, days in (("Person 1", -3), ("Person 2", 10), ("Person 3", 200)):
            s.add(
                AccountException(
                    tenant_id=t.id,
                    identity_id=rows[name].id,
                    reason="x",
                    created_by="t",
                    valid_until=NOW + timedelta(days=days),
                )
            )
            ids.append(name)
    with factory() as s:
        items = list_exceptions(s, get_tenant(s), NOW)
        assert [(i.display_name, i.expired, i.expires_soon) for i in items] == [
            ("Person 1", True, False),
            ("Person 2", False, True),  # 10 Tage < Vorwarnzeit 30
            ("Person 3", False, False),
        ]


def test_list_is_scoped_to_the_tenant(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        identity_id = _inactive_identity(s, "cvh")
        s.add(
            AccountException(
                tenant_id=get_tenant(s, "cvh").id,
                identity_id=identity_id,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=5),
            )
        )
    with factory() as s:
        assert list_exceptions(s, get_tenant(s, "cg"), NOW) == []
        assert len(list_exceptions(s, get_tenant(s, "cvh"), NOW)) == 1
        assert s.scalars(select(Identity)).all()
