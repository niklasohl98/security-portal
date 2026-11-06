from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core import cycle
from app.core.ad_sync import AdSyncResult
from app.core.cycle import run_all, run_nightly, run_tenant_cycle
from app.core.sync import SyncBusyError, SyncError, SyncResult
from app.db.models import RunStatus, Source, SyncRun
from tests.identity_fakes import ago, get_tenant, make_entra, make_run


def _entra_ok(run_id: int = 1) -> SyncResult:
    return SyncResult(run_id, RunStatus.SUCCESS, 1, 0, [], None)


class Calls:
    """Zeichnet die Aufrufe der ersetzten Abrufe auf."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    c = Calls()

    def fake_entra(_f: Any, slug: str, _secrets: Any, **_kw: Any) -> SyncResult:
        c.log.append(("entra", slug))
        return _entra_ok()

    def fake_ad(_f: Any, slug: str, *_a: Any, **_kw: Any) -> AdSyncResult:
        c.log.append(("ad", slug))
        return AdSyncResult(2, RunStatus.SUCCESS, 1, 0, [], None)

    monkeypatch.setattr(cycle, "run_entra_sync", fake_entra)
    monkeypatch.setattr(cycle, "run_ad_sync", fake_ad)
    return c


def test_cycle_fetches_configured_sources_in_order_then_evaluates(
    factory: sessionmaker[Session], settings: Settings, calls: Calls
) -> None:
    with factory.begin() as s:  # ein brauchbarer Lauf, damit die Auswertung etwas findet
        make_entra(s, make_run(s, get_tenant(s), Source.ENTRA), 1, last_successful_sign_in=ago(1))
    result = run_tenant_cycle(factory, settings, "cg")
    assert calls.log == [("entra", "cg"), ("ad", "cg")]
    assert [(o.source, o.status) for o in result.outcomes] == [
        (Source.ENTRA, "success"),
        (Source.AD, "success"),
    ]
    assert result.refresh is not None
    assert result.refresh.evaluated is True


def test_one_failing_source_does_not_stop_the_other_or_the_evaluation(
    factory: sessionmaker[Session],
    settings: Settings,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> SyncResult:
        raise RuntimeError("geheimer Inhalt")

    monkeypatch.setattr(cycle, "run_entra_sync", boom)
    with factory.begin() as s:
        make_entra(s, make_run(s, get_tenant(s), Source.ENTRA), 1, last_successful_sign_in=ago(1))
    result = run_tenant_cycle(factory, settings, "cg")
    assert [(o.source, o.status) for o in result.outcomes] == [
        (Source.ENTRA, "error"),
        (Source.AD, "success"),
    ]
    assert result.refresh is not None
    assert result.refresh.evaluated is True


@pytest.mark.parametrize(
    ("exc", "status"), [(SyncBusyError("x"), "busy"), (SyncError("x"), "skipped")]
)
def test_sync_errors_become_outcome_codes(
    factory: sessionmaker[Session],
    settings: Settings,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status: str,
) -> None:
    def raiser(*_a: Any, **_k: Any) -> SyncResult:
        raise exc

    monkeypatch.setattr(cycle, "run_entra_sync", raiser)
    result = run_tenant_cycle(factory, settings, "cg")
    assert result.outcomes[0].status == status


def test_unknown_and_disabled_tenants_are_skipped(
    factory: sessionmaker[Session], settings: Settings, calls: Calls
) -> None:
    assert run_tenant_cycle(factory, settings, "gibt-es-nicht").skipped is True
    with factory.begin() as s:
        get_tenant(s, "cvh").enabled = False
    assert run_tenant_cycle(factory, settings, "cvh").skipped is True
    assert calls.log == []


def test_a_running_cycle_is_not_stacked(
    factory: sessionmaker[Session],
    settings: Settings,
    calls: Calls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(_f: Any, slug: str, _s: Any, **_k: Any) -> SyncResult:
        started.set()
        release.wait(5)
        return _entra_ok()

    monkeypatch.setattr(cycle, "run_entra_sync", slow)
    first = threading.Thread(target=run_tenant_cycle, args=(factory, settings, "cg"))
    first.start()
    assert started.wait(5)
    try:
        assert run_tenant_cycle(factory, settings, "cg").skipped is True
    finally:
        release.set()
        first.join(5)


def test_run_all_visits_enabled_tenants_alphabetically(
    factory: sessionmaker[Session], settings: Settings, calls: Calls
) -> None:
    results = run_all(factory, settings)
    assert [r.slug for r in results] == ["cg", "cvh"]


def test_run_all_is_skipped_while_another_run_all_is_active(
    factory: sessionmaker[Session], settings: Settings, calls: Calls
) -> None:
    lock = cycle._lock(cycle._ALL)
    assert lock.acquire(blocking=False)
    try:
        assert run_all(factory, settings) == []
        assert run_nightly(factory, settings) is None
    finally:
        lock.release()


def test_nightly_purges_old_raw_data_after_the_cycle(
    factory: sessionmaker[Session], settings: Settings, calls: Calls
) -> None:
    with factory.begin() as s:
        t = get_tenant(s)
        old = make_run(s, t, Source.ENTRA)
        old.started_at = ago(400)
        make_entra(s, old, 1)
        new = make_run(s, t, Source.ENTRA)
        make_entra(s, new, 2)
    result = run_nightly(factory, settings)
    assert result is not None
    assert result.entra_accounts == 1
    with factory() as s:
        assert len(s.query(SyncRun).all()) == 2
