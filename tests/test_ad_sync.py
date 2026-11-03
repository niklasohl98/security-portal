from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, Settings, load_tenants
from app.connectors.ad_client import ERR_CONNECT, AdError, Directory, TlsPolicy
from app.core.ad_sync import (
    WARN_WEAK_KEY_ACTIVE,
    WARN_WEAK_KEY_EXPIRED,
    AdSyncResult,
    build_ad_runtime,
    run_ad_sync,
)
from app.core.sync import SyncBusyError, SyncError, _lock_for
from app.core.tenants import sync_tenants
from app.db.models import AdAccount, RunStatus, Source, SyncRun, Tenant
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.ad_fakes import FakeDirectory, opener_for, user
from tests.conftest import EXAMPLE_TENANTS

T1 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
PASSWORD = "geheimes-testpasswort"
HOSTS = ["dc1.example.invalid", "dc2.example.invalid"]


@pytest.fixture
def factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    f = make_session_factory(engine)
    with f.begin() as s:
        sync_tenants(s, load_tenants(EXAMPLE_TENANTS))
    settings.secrets_dir.mkdir(exist_ok=True)
    (settings.secrets_dir / "cg_ad_bind").write_text(PASSWORD + "\n", encoding="utf-8")
    yield f
    engine.dispose()


def _dc(**kw: object) -> FakeDirectory:
    return FakeDirectory([user(1, logon_ts=T1), user(2), user(3)], {1: T1}, **kw)  # type: ignore[arg-type]


def _run(
    factory: sessionmaker[Session],
    settings: Settings,
    dcs: dict[str, Directory | AdError],
    slug: str = "cg",
) -> AdSyncResult:
    return run_ad_sync(
        factory,
        slug,
        settings.secrets_dir,
        settings.tenants_file,
        opener=opener_for(dcs),
        resolver=lambda _d: sorted(dcs),
    )


def test_successful_run_stores_accounts_and_run(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    result = _run(factory, settings, {HOSTS[0]: _dc(), HOSTS[1]: _dc()})

    assert result.status is RunStatus.SUCCESS
    assert result.account_count == 3
    assert result.warnings == []
    with factory() as s:
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert (run.source, run.status, run.account_count) == (Source.AD, RunStatus.SUCCESS, 3)
        assert (run.dcs_checked, run.dcs_defective, run.finished_at is not None) == (2, [], True)
        rows = list(s.scalars(select(AdAccount).where(AdAccount.sync_run_id == run.id)))
        cg_id = s.scalars(select(Tenant.id).where(Tenant.slug == "cg")).one()
        assert {r.tenant_id for r in rows} == {cg_id}
        first = next(r for r in rows if r.sam_account_name == "user001")
        assert first.last_logon == T1
        assert first.last_logon_per_dc.keys() == set(HOSTS)
        assert first.when_created == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        assert first.last_logon_timestamp == T1


def test_tenants_are_separated(factory: sessionmaker[Session], settings: Settings) -> None:
    (settings.secrets_dir / "cvh_ad_bind").write_text("x", encoding="utf-8")
    _run(factory, settings, {HOSTS[0]: _dc()}, slug="cg")
    with factory() as s:
        cvh_id = s.scalars(select(Tenant.id).where(Tenant.slug == "cvh")).one()
        assert (
            s.scalar(
                select(func.count()).select_from(AdAccount).where(AdAccount.tenant_id == cvh_id)
            )
            == 0
        )


def test_dc_problems_make_the_run_partial_and_are_stored(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    broken = FakeDirectory([user(1, uac=None, created=None)], refs={"svc-kontenwaechter": False})
    dcs: dict[str, Directory | AdError] = {
        "dc1.example.invalid": broken,
        "dc2.example.invalid": _dc(),
        "dc3.example.invalid": AdError(ERR_CONNECT),
    }
    result = _run(factory, settings, dcs)

    assert result.status is RunStatus.PARTIAL
    assert result.warnings == ["ad_dc_unreachable", "ad_dc_defective"]
    with factory() as s:
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert run.dcs_checked == 3
        assert run.dcs_defective == [
            {"host": "dc1.example.invalid", "status": "defective", "reason": "reference_empty"},
            {"host": "dc3.example.invalid", "status": "unreachable", "reason": "connect"},
        ]


def test_no_healthy_dc_fails_the_run_with_dc_details(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    result = _run(factory, settings, {HOSTS[0]: AdError(ERR_CONNECT), HOSTS[1]: AdError("tls")})

    assert result.status is RunStatus.FAILED
    assert result.error == "AdError code=no_healthy_dc"
    with factory() as s:
        run = s.get(SyncRun, result.run_id)
        assert run is not None
        assert (run.status, run.account_count, run.dcs_checked) == (RunStatus.FAILED, 0, 2)
        assert [d["reason"] for d in run.dcs_defective] == ["connect", "tls"]
        assert s.scalar(select(func.count()).select_from(AdAccount)) == 0


def test_srv_failure_fails_the_run_without_leaking(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    def no_srv(_domain: str) -> list[str]:
        raise AdError("no_dc_found")

    result = run_ad_sync(
        factory, "cg", settings.secrets_dir, settings.tenants_file, resolver=no_srv
    )
    assert (result.status, result.error) == (RunStatus.FAILED, "AdError code=no_dc_found")


def test_unexpected_error_is_recorded_as_type_only(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    def boom(_host: str) -> Directory:
        raise RuntimeError(f"enthält {PASSWORD}")

    result = run_ad_sync(
        factory,
        "cg",
        settings.secrets_dir,
        settings.tenants_file,
        opener=boom,
        resolver=lambda _d: HOSTS,
    )
    assert (result.status, result.error) == (RunStatus.FAILED, "RuntimeError")
    assert PASSWORD not in (result.error or "")


def test_missing_secret_fails_the_run(factory: sessionmaker[Session], settings: Settings) -> None:
    (settings.secrets_dir / "cg_ad_bind").unlink()
    result = run_ad_sync(factory, "cg", settings.secrets_dir, settings.tenants_file)
    assert result.status is RunStatus.FAILED
    assert result.error == "Secret 'cg_ad_bind' nicht lesbar"


def test_logs_contain_no_account_data_or_password(
    factory: sessionmaker[Session], settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _run(factory, settings, {HOSTS[0]: _dc(), HOSTS[1]: AdError(ERR_CONNECT)})
    text = caplog.text
    assert "AD-Abruf fertig" in text
    assert "AD-DC geprüft" in text
    for forbidden in ("user001", "beispiel.invalid", "Nutzer", PASSWORD):
        assert forbidden not in text


def test_busy_lock_raises(factory: sessionmaker[Session], settings: Settings) -> None:
    with factory() as s:
        tenant_id = s.scalars(select(Tenant.id).where(Tenant.slug == "cg")).one()
    lock = _lock_for(tenant_id, Source.AD)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(SyncBusyError):
            _run(factory, settings, {HOSTS[0]: _dc()})
    finally:
        lock.release()


def test_entra_lock_does_not_block_ad(factory: sessionmaker[Session], settings: Settings) -> None:
    with factory() as s:
        tenant_id = s.scalars(select(Tenant.id).where(Tenant.slug == "cg")).one()
    lock = _lock_for(tenant_id, Source.ENTRA)
    assert lock.acquire(blocking=False)
    try:
        assert _run(factory, settings, {HOSTS[0]: _dc()}).status is RunStatus.SUCCESS
    finally:
        lock.release()


def test_unknown_disabled_or_entra_only_tenant_is_refused(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        s.add(Tenant(slug="nur-entra", name="E"))
        s.scalars(select(Tenant).where(Tenant.slug == "cvh")).one().enabled = False
    for slug in ("gibt-es-nicht", "cvh", "nur-entra"):
        with pytest.raises(SyncError):
            run_ad_sync(factory, slug, settings.secrets_dir, settings.tenants_file)


# --- build_ad_runtime: CA-Datei und befristete Ausnahme ---------------------------------------


def _tenant_with(tmp_path: Path, extra: str) -> tuple[Tenant, Path]:
    (tmp_path / "ca").mkdir(exist_ok=True)
    (tmp_path / "ca" / "root.pem").write_text("PEM", encoding="utf-8")
    yaml = f"""
tenants:
  - slug: t
    name: T
    ad:
      domain: beispiel.invalid
      bind_user: svc
      bind_secret: bind
{extra}
"""
    tenants_file = tmp_path / "tenants.yaml"
    tenants_file.write_text(yaml, encoding="utf-8")
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    (secrets / "bind").write_text(PASSWORD, encoding="utf-8")
    ad = load_tenants(tenants_file)[0].ad
    assert ad is not None
    row = Tenant(
        slug="t",
        name="T",
        ad_domain=ad.domain,
        ad_dcs=[],
        ad_bind_user=ad.bind_user,
        ad_bind_secret=ad.bind_secret,
        ad_ca_file=ad.ca_file,
        ad_weak_keys_until=ad.accept_weak_dc_keys_until,
    )
    return row, tenants_file


def _policy_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: str, today: date
) -> tuple[TlsPolicy, list[str]]:
    seen: list[TlsPolicy] = []

    def fake_open(host: str, *, policy: TlsPolicy, **_kw: object) -> Directory:
        seen.append(policy)
        return FakeDirectory([])

    monkeypatch.setattr("app.core.ad_sync.open_ldaps", fake_open)
    row, tenants_file = _tenant_with(tmp_path, extra)
    runtime = build_ad_runtime(row, tmp_path / "secrets", tenants_file, today)
    runtime.opener("dc1.example.invalid")
    return seen[0], runtime.notices


def test_default_policy_is_strict_and_uses_system_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy, notices = _policy_used(monkeypatch, tmp_path, "", date(2026, 11, 3))
    assert policy == TlsPolicy(ca_file=None, allow_weak_keys=False)
    assert notices == []


def test_ca_file_is_resolved_relative_to_config_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy, _ = _policy_used(monkeypatch, tmp_path, "      ca_file: ca/root.pem", date(2026, 11, 3))
    assert policy.ca_file == (tmp_path / "ca" / "root.pem").resolve()


def test_weak_key_exception_is_active_until_the_end_date_inclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extra = "      accept_weak_dc_keys_until: 2026-12-31"
    policy, notices = _policy_used(monkeypatch, tmp_path, extra, date(2026, 12, 31))
    assert (policy.allow_weak_keys, notices) == (True, [WARN_WEAK_KEY_ACTIVE])


def test_weak_key_exception_lapses_after_the_end_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    extra = "      accept_weak_dc_keys_until: 2026-12-31"
    policy, notices = _policy_used(monkeypatch, tmp_path, extra, date(2027, 1, 1))
    assert (policy.allow_weak_keys, notices) == (False, [WARN_WEAK_KEY_EXPIRED])


def test_missing_ca_file_is_a_config_error(tmp_path: Path) -> None:
    row, tenants_file = _tenant_with(tmp_path, "      ca_file: ca/fehlt.pem")
    with pytest.raises(ConfigError):
        build_ad_runtime(row, tmp_path / "secrets", tenants_file)


def test_service_account_and_dcs_come_from_the_tenant(tmp_path: Path) -> None:
    row, tenants_file = _tenant_with(tmp_path, "")
    row.ad_dcs = ["dc9.example.invalid"]
    runtime = build_ad_runtime(row, tmp_path / "secrets", tenants_file)
    assert (runtime.service_account, runtime.configured_dcs) == ("svc", ["dc9.example.invalid"])
    assert runtime.domain == "beispiel.invalid"


def test_runtime_requires_ad_configuration(tmp_path: Path) -> None:
    with pytest.raises(SyncError):
        build_ad_runtime(Tenant(slug="x", name="X"), tmp_path, tmp_path / "tenants.yaml")
