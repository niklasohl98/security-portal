from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.review import Decision, ReviewError, decide, list_missing
from app.db.models import AccountException, AuditLog, Identity
from tests.identity_fakes import NOW, ago, get_tenant

ACTOR = "test-akteur"


def _missing(s: Session, slug: str, name: str, days: float = 5, **kw: object) -> Identity:
    row = Identity(
        tenant_id=get_tenant(s, slug).id,
        key=f"{slug}-{name}",
        display_name=name,
        status="missing",
        missing_since=ago(days),
        **kw,
    )
    s.add(row)
    s.flush()
    return row


def _exception(s: Session, row: Identity, n: int = 1) -> None:
    for _ in range(n):
        s.add(
            AccountException(
                tenant_id=row.tenant_id,
                identity_id=row.id,
                reason="x",
                created_by="t",
                valid_until=NOW + timedelta(days=30),
            )
        )
    s.flush()


def test_list_shows_open_items_oldest_first_with_exception_counts(
    factory: sessionmaker[Session],
) -> None:
    with factory.begin() as s:
        _exception(s, _missing(s, "cg", "Neuer", days=2), 2)
        _missing(s, "cg", "Aelter", days=20)
        _missing(s, "cg", "Entschieden", days=9, review_decision="keep", reviewed_at=ago(1))
        s.add(Identity(tenant_id=get_tenant(s).id, key="da", display_name="Aktiv"))
    with factory.begin() as s:
        items = list_missing(s, get_tenant(s))
        assert [(i.display_name, i.exception_count) for i in items] == [("Aelter", 0), ("Neuer", 2)]
        decided = list_missing(s, get_tenant(s), decided=True)
        assert [(i.display_name, i.decided) for i in decided] == [("Entschieden", True)]


def test_list_never_shows_other_tenants(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        _missing(s, "cg", "Eigen")
        _missing(s, "cvh", "Fremd")
    with factory.begin() as s:
        assert [i.display_name for i in list_missing(s, get_tenant(s, "cg"))] == ["Eigen"]
        assert [i.display_name for i in list_missing(s, get_tenant(s, "cvh"))] == ["Fremd"]


def test_keep_then_reopen(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        row_id = _missing(s, "cg", "A").id
    with factory.begin() as s:
        decide(s, get_tenant(s), row_id, Decision.KEEP, ACTOR, NOW)
    with factory.begin() as s:
        row = s.get(Identity, row_id)
        assert row is not None
        assert (row.review_decision, row.reviewed_at) == ("keep", NOW)
        assert list_missing(s, get_tenant(s)) == []
        with pytest.raises(ReviewError) as err:
            decide(s, get_tenant(s), row_id, Decision.KEEP, ACTOR, NOW)
        assert err.value.code == "bad_state"
    with factory.begin() as s:
        decide(s, get_tenant(s), row_id, Decision.REOPEN, ACTOR, NOW)
    with factory.begin() as s:
        row = s.get(Identity, row_id)
        assert row is not None
        assert (row.review_decision, row.reviewed_at) == (None, None)
        assert len(list_missing(s, get_tenant(s))) == 1


def test_delete_removes_the_identity_and_its_exceptions(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        row = _missing(s, "cg", "Geheimer Name")
        _exception(s, row, 2)
        other = _missing(s, "cg", "Bleibt")
        _exception(s, other)
        row_id = row.id
    with factory.begin() as s:
        decide(s, get_tenant(s), row_id, Decision.DELETE, ACTOR, NOW)
    with factory.begin() as s:
        assert s.get(Identity, row_id) is None
        assert len(s.scalars(select(AccountException)).all()) == 1  # nur die der anderen Zeile
        (entry,) = s.scalars(select(AuditLog)).all()
        assert (entry.actor, entry.action, entry.at) == (ACTOR, "identity.delete", NOW)
        assert entry.detail == f"identity_id={row_id} ausnahmen=2"
        assert entry.tenant_id == get_tenant(s).id
        assert "Geheimer" not in (entry.detail or "")  # keine Namen im Audit-Log


def test_a_kept_identity_can_still_be_deleted(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        row_id = _missing(s, "cg", "A", review_decision="keep", reviewed_at=ago(1)).id
    with factory.begin() as s:
        decide(s, get_tenant(s), row_id, Decision.DELETE, ACTOR, NOW)
    with factory.begin() as s:
        assert s.get(Identity, row_id) is None


def test_every_decision_is_written_to_the_audit_log(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        row_id = _missing(s, "cg", "A").id
    for decision in (Decision.KEEP, Decision.REOPEN, Decision.DELETE):
        with factory.begin() as s:
            decide(s, get_tenant(s), row_id, decision, ACTOR, NOW)
    with factory.begin() as s:
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert actions == ["identity.keep", "identity.reopen", "identity.delete"]


def test_decisions_are_refused_for_wrong_state_or_tenant(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        active = Identity(tenant_id=get_tenant(s).id, key="a", display_name="Aktiv")
        s.add(active)
        foreign = _missing(s, "cvh", "Fremd")
        open_item = _missing(s, "cg", "Offen")
        s.flush()
        active_id, foreign_id, open_id = active.id, foreign.id, open_item.id
    with factory.begin() as s:
        cg = get_tenant(s)
        for identity_id, code in (
            (active_id, "not_missing"),
            (foreign_id, "not_found"),
            (9999, "not_found"),
        ):
            with pytest.raises(ReviewError) as err:
                decide(s, cg, identity_id, Decision.DELETE, ACTOR, NOW)
            assert err.value.code == code
        with pytest.raises(ReviewError) as err:
            decide(s, cg, open_id, Decision.REOPEN, ACTOR, NOW)  # nichts zu öffnen
        assert err.value.code == "bad_state"
    with factory.begin() as s:
        assert s.get(Identity, foreign_id) is not None  # fremder Mandant unangetastet
        assert s.scalars(select(AuditLog)).all() == []  # abgelehnte Aktionen nicht im Log
