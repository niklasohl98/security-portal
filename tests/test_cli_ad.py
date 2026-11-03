from __future__ import annotations

from types import SimpleNamespace

import dns.resolver
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.cli import main as cli_main
from app.config import Settings
from app.connectors.ad_client import ERR_CONNECT, AdError, Directory
from app.core.ad_sync import AdRuntime
from app.db.models import AdAccount
from app.db.session import create_db_engine, make_session_factory
from app.main import create_app
from tests.ad_fakes import FakeDirectory, opener_for, user

HOSTS = ["dc1.example.invalid", "dc2.example.invalid"]


@pytest.fixture
def env(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    with TestClient(create_app(settings)):
        pass
    monkeypatch.setenv("KW_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("KW_SECRETS_DIR", str(settings.secrets_dir))
    monkeypatch.setenv("KW_TENANTS_FILE", str(settings.tenants_file))
    settings.secrets_dir.mkdir(exist_ok=True)
    (settings.secrets_dir / "cg_ad_bind").write_text("pw", encoding="utf-8")
    return settings


def _dcs() -> dict[str, Directory | AdError]:
    return {
        HOSTS[0]: FakeDirectory([user(1), user(2)]),
        HOSTS[1]: AdError(ERR_CONNECT),
    }


def _use_fake_ldaps(monkeypatch: pytest.MonkeyPatch, dcs: dict[str, Directory | AdError]) -> None:
    opener = opener_for(dcs)
    monkeypatch.setattr("app.core.ad_sync.open_ldaps", lambda host, **_kw: opener(host))
    monkeypatch.setattr(
        dns.resolver.Resolver,
        "resolve",
        lambda *_a, **_k: [SimpleNamespace(target=f"{h}.") for h in dcs],
    )


def test_check_ad_prints_dcs_and_stores_nothing(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_fake_ldaps(monkeypatch, _dcs())
    assert cli_main(["check-ad", "cg"]) == 0
    out = capsys.readouterr().out
    assert "DCs gefunden: 2" in out
    assert "dc1.example.invalid\tok\t-\t2 Konten" in out
    assert "dc2.example.invalid\tunreachable\tconnect\t-" in out
    assert "Konten: 2 (Stammdaten von dc1.example.invalid)" in out
    assert "Warnungen: ad_dc_unreachable" in out
    assert "user001" not in out  # keine Kontodaten in der Ausgabe

    engine = create_db_engine(env.database_url)
    with make_session_factory(engine)() as s:
        assert s.scalar(select(func.count()).select_from(AdAccount)) == 0
    engine.dispose()


def test_check_ad_with_configured_dcs_skips_srv(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = AdRuntime(
        domain="x",
        configured_dcs=[HOSTS[0]],
        service_account="svc-kontenwaechter",
        opener=opener_for({HOSTS[0]: FakeDirectory([user(1)])}),
        notices=["ad_weak_key_exception_active"],
    )
    monkeypatch.setattr("app.cli.build_ad_runtime", lambda *_a: fake)
    assert cli_main(["check-ad", "cg"]) == 0
    out = capsys.readouterr().out
    assert "DCs gefunden: 1" in out
    assert "Warnungen: ad_weak_key_exception_active" in out


def test_check_ad_errors(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["check-ad", "gibt-es-nicht"]) == 2
    assert "unbekannt" in capsys.readouterr().out

    _use_fake_ldaps(monkeypatch, {HOSTS[0]: AdError(ERR_CONNECT)})
    assert cli_main(["check-ad", "cg"]) == 1
    out = capsys.readouterr().out
    assert "FEHLER: AdError code=no_healthy_dc" in out
    assert "dc1.example.invalid\tunreachable\tconnect" in out  # Grund je DC sichtbar

    (env.secrets_dir / "cg_ad_bind").unlink()
    assert cli_main(["check-ad", "cg"]) == 1
    assert "Secret 'cg_ad_bind' nicht lesbar" in capsys.readouterr().out


def test_sync_ad_stores_accounts_and_prints_result(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_fake_ldaps(monkeypatch, _dcs())
    assert cli_main(["sync-ad", "cg"]) == 0
    out = capsys.readouterr().out
    assert "partial" in out
    assert "Konten: 2" in out
    assert "dc2.example.invalid\tunreachable" in out
    assert "Warnungen: ad_dc_unreachable" in out

    engine = create_db_engine(env.database_url)
    with make_session_factory(engine)() as s:
        assert s.scalar(select(func.count()).select_from(AdAccount)) == 2
    engine.dispose()


def test_sync_ad_failure_exit_codes(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["sync-ad", "gibt-es-nicht"]) == 2
    assert "FEHLER" in capsys.readouterr().out

    _use_fake_ldaps(monkeypatch, {HOSTS[0]: AdError(ERR_CONNECT)})
    assert cli_main(["sync-ad", "cg"]) == 1
    out = capsys.readouterr().out
    assert "failed" in out
    assert "Fehler: AdError code=no_healthy_dc" in out
