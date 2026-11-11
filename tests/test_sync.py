from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, load_tenants
from app.connectors.graph import GraphClient
from app.core.source_references import record_entra_accounts
from app.core.sync import (
    WARN_NO_SIGN_IN,
    SyncBusyError,
    SyncError,
    build_graph_client,
    fail_stale_runs,
    run_entra_sync,
)
from app.core.tenants import sync_tenants
from app.db.models import (
    DirectorySource,
    EntraAccount,
    RunStatus,
    Source,
    SourceAccount,
    SyncRun,
    Tenant,
    TenantSku,
)
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.conftest import EXAMPLE_TENANTS
from tests.graph_fakes import BASE, FakeTokens, load, make_users


@pytest.fixture
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    f = make_session_factory(engine)
    with f.begin() as s:
        sync_tenants(s, load_tenants(EXAMPLE_TENANTS))
    yield f
    engine.dispose()


def _client() -> GraphClient:
    return GraphClient(FakeTokens(), sleep=lambda _s: None)


def _mock_ok(users: list[dict[str, Any]] | None = None) -> None:
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )
    respx.get(f"{BASE}/users").mock(
        return_value=httpx.Response(200, json={"value": users or load("graph_users")["value"]})
    )


@respx.mock
def test_source_references_survive_repeated_runs_and_are_tenant_scoped(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    _mock_ok()
    run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    with factory() as session:
        before = {row.external_id: row.id for row in session.scalars(select(SourceAccount))}
        assert len(before) == 5
    run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    run_entra_sync(factory, "cvh", settings.secrets_dir, client=_client())
    with factory() as session:
        cg = session.scalars(select(Tenant).where(Tenant.slug == "cg")).one()
        current = {
            row.external_id: row.id
            for row in session.scalars(
                select(SourceAccount).where(SourceAccount.tenant_id == cg.id)
            )
        }
        assert current == before
        assert len(session.scalars(select(DirectorySource)).all()) == 2
        assert len(session.scalars(select(SourceAccount)).all()) == 10


def test_invalid_source_references_are_not_recorded(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == "cg")).one()
        record_entra_accounts(session, tenant.id, "invalid", ["invalid"])
        assert session.scalars(select(DirectorySource)).all() == []
        assert tenant.entra_tenant_id is not None
        record_entra_accounts(session, tenant.id, tenant.entra_tenant_id, ["invalid"])
        session.flush()
        assert len(session.scalars(select(DirectorySource)).all()) == 1
        assert session.scalars(select(SourceAccount)).all() == []


@respx.mock
def test_successful_run_stores_accounts_skus_and_status(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    _mock_ok()
    result = run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    assert (result.status, result.account_count, result.sku_count) == (RunStatus.SUCCESS, 5, 3)
    with factory() as s:
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert (run.source, run.status, run.account_count) == (Source.ENTRA, RunStatus.SUCCESS, 5)
        assert run.finished_at is not None
        assert run.finished_at.tzinfo is not None
        assert run.warnings == []
        accounts = list(s.scalars(select(EntraAccount)))
        assert len(accounts) == 5
        erika = next(a for a in accounts if a.on_prem_sam_account_name == "ebeispiel")
        assert erika.last_successful_sign_in is not None
        assert erika.licenses[0]["direct"] is True
        assert {t.part_number for t in s.scalars(select(TenantSku))} == {
            "SPE_E3",
            "EXCHANGEENTERPRISE",
            "FLOW_FREE",
        }


@respx.mock
def test_651_accounts_in_one_run(factory: sessionmaker[Session], settings: Settings) -> None:
    _mock_ok(make_users(651))
    result = run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    assert result.account_count == 651


@respx.mock
def test_missing_p1_gives_partial_with_warning(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "signInActivity" in request.url.params["$select"]:
            return httpx.Response(403, json={"error": {"code": "Forbidden"}})
        return httpx.Response(200, json={"value": make_users(3)})

    respx.get(f"{BASE}/users").mock(side_effect=handler)
    result = run_entra_sync(factory, "cvh", settings.secrets_dir, client=_client())
    assert result.status is RunStatus.PARTIAL
    assert result.warnings == [WARN_NO_SIGN_IN]
    with factory() as s:
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert run.warnings == [WARN_NO_SIGN_IN]


@respx.mock
def test_graph_failure_is_recorded_not_raised_and_stores_no_accounts(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {"code": "InvalidAuthenticationToken", "message": "max.muster@x.invalid"}
            },
        )
    )
    result = run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    assert result.status is RunStatus.FAILED
    assert result.error == "GraphError status=401 code=InvalidAuthenticationToken"
    with factory() as s:
        assert s.scalars(select(EntraAccount)).first() is None
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert run.status is RunStatus.FAILED
        assert run.finished_at is not None
        assert "muster" not in (run.error or "")


def test_unexpected_error_is_recorded_with_type_only(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    class Boom(GraphClient):
        def pages(self, path: str, params: dict[str, str] | None = None) -> Any:
            raise RuntimeError("enthält max.muster@x.invalid")

    result = run_entra_sync(
        factory, "cg", settings.secrets_dir, client=Boom(FakeTokens(), sleep=lambda _s: None)
    )
    assert result.status is RunStatus.FAILED
    assert result.error == "RuntimeError"


def test_missing_secret_fails_the_run_without_leaking(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    result = run_entra_sync(factory, "cg", settings.secrets_dir)  # Secret-Datei existiert nicht
    assert result.status is RunStatus.FAILED
    assert result.error == "ConfigError"


@pytest.mark.parametrize("slug", ["gibt-es-nicht"])
def test_unknown_tenant_raises(
    factory: sessionmaker[Session], settings: Settings, slug: str
) -> None:
    with pytest.raises(SyncError):
        run_entra_sync(factory, slug, settings.secrets_dir, client=_client())


def test_disabled_tenant_raises_and_creates_no_run(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        s.scalars(select(Tenant).where(Tenant.slug == "cg")).one().enabled = False
    with pytest.raises(SyncError):
        run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    with factory() as s:
        assert s.scalars(select(SyncRun)).first() is None


def test_tenant_without_entra_raises(factory: sessionmaker[Session], settings: Settings) -> None:
    with factory.begin() as s:
        s.add(Tenant(slug="nur-ad", name="Nur AD", ad_domain="x.invalid"))
    with pytest.raises(SyncError):
        run_entra_sync(factory, "nur-ad", settings.secrets_dir, client=_client())


@respx.mock
def test_second_run_while_first_is_running_is_refused(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(_request: httpx.Request) -> httpx.Response:
        started.set()
        release.wait(5)
        return httpx.Response(200, json=load("graph_skus"))

    respx.get(f"{BASE}/subscribedSkus").mock(side_effect=slow)
    respx.get(f"{BASE}/users").mock(return_value=httpx.Response(200, json={"value": []}))
    first: list[Any] = []
    t = threading.Thread(
        target=lambda: first.append(
            run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
        )
    )
    t.start()
    assert started.wait(5)
    with pytest.raises(SyncBusyError):
        run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    # anderer Mandant ist nicht blockiert
    other = run_entra_sync(factory, "cvh", settings.secrets_dir, client=_client())
    release.set()
    t.join(5)
    assert other.status in (RunStatus.SUCCESS, RunStatus.FAILED)
    assert first[0].status is RunStatus.SUCCESS
    # Sperre ist wieder frei
    release.set()
    again = run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    assert again.run_id != first[0].run_id


def test_every_row_carries_the_tenant_of_its_run(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with respx.mock:
        _mock_ok()
        run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
        run_entra_sync(factory, "cvh", settings.secrets_dir, client=_client())
    with factory() as s:
        ids = {t.slug: t.id for t in s.scalars(select(Tenant))}
        for acc in s.scalars(select(EntraAccount)):
            run = s.get(SyncRun, acc.sync_run_id)
            assert run is not None
            assert run.tenant_id == acc.tenant_id
        per_tenant = {
            slug: len(list(s.scalars(select(EntraAccount).where(EntraAccount.tenant_id == tid))))
            for slug, tid in ids.items()
        }
    assert per_tenant == {"cg": 5, "cvh": 5}


def test_fail_stale_runs(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        tid = s.scalars(select(Tenant)).first()
        assert tid is not None
        s.add(SyncRun(tenant_id=tid.id, source=Source.ENTRA, status=RunStatus.RUNNING))
        s.add(SyncRun(tenant_id=tid.id, source=Source.ENTRA, status=RunStatus.SUCCESS))
    with factory.begin() as s:
        assert fail_stale_runs(s) == 1
    with factory() as s:
        statuses = sorted(r.status.value for r in s.scalars(select(SyncRun)))
    assert statuses == ["failed", "success"]


def test_build_graph_client_reads_secret_and_requires_entra(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeProvider:
        def __init__(self, *args: str) -> None:
            captured["args"] = args

        def get_token(self) -> str:
            return "t"

    monkeypatch.setattr("app.core.sync.MsalCertificateTokenProvider", FakeProvider)
    settings.secrets_dir.mkdir()
    (settings.secrets_dir / "cg_entra_cert").write_text("PEM-INHALT\n", encoding="utf-8")
    with factory() as s:
        cg = s.scalars(select(Tenant).where(Tenant.slug == "cg")).one()
        assert isinstance(build_graph_client(cg, settings.secrets_dir), GraphClient)
        assert captured["args"][2] == "PEM-INHALT"
        assert captured["args"][3] == cg.entra_certificate_thumbprint
        s.expunge(cg)
        cg.entra_client_id = None
        with pytest.raises(SyncError):
            build_graph_client(cg, settings.secrets_dir)


@respx.mock
def test_logs_contain_no_account_data(
    factory: sessionmaker[Session], settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _mock_ok()
    run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(
            500, json={"error": {"code": "X", "message": "erika.beispiel@cg.example.invalid"}}
        )
    )
    run_entra_sync(factory, "cg", settings.secrets_dir, client=_client())
    text = " ".join(r.getMessage() for r in caplog.records)
    for forbidden in ("erika", "beispiel", "muster", "example.invalid", "test-token", "Bearer"):
        assert forbidden not in text.lower()
    assert "konten=5" in text


def test_app_start_marks_interrupted_runs_failed(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)):
        pass  # Datenbank und Mandanten anlegen
    engine = create_db_engine(settings.database_url)
    f = make_session_factory(engine)
    with f.begin() as s:
        tenant = s.scalars(select(Tenant)).first()
        assert tenant is not None
        s.add(SyncRun(tenant_id=tenant.id, source=Source.ENTRA, status=RunStatus.RUNNING))
    with TestClient(create_app(settings)):
        pass  # zweiter Start schließt den unterbrochenen Lauf ab
    with f() as s:
        run = s.scalars(select(SyncRun)).one()
    engine.dispose()
    assert run.status is RunStatus.FAILED
    assert run.error == "abgebrochen (Neustart)"
