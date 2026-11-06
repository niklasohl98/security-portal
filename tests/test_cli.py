from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.cli import main as cli_main
from app.config import Settings
from app.connectors.graph import GraphClient, GraphError
from app.main import create_app
from tests.graph_fakes import BASE, FakeTokens, load


@pytest.fixture
def env(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Datenbank mit Mandanten anlegen und die CLI per Umgebungsvariablen darauf zeigen lassen."""
    with TestClient(create_app(settings)):
        pass
    monkeypatch.setenv("KW_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("KW_SECRETS_DIR", str(settings.secrets_dir))
    return settings


def _fake_client(*_args: object) -> GraphClient:
    return GraphClient(FakeTokens(), sleep=lambda _s: None)


def _mock_graph(users_status: int = 200) -> None:
    respx.get(f"{BASE}/organization").mock(
        return_value=httpx.Response(200, json=load("graph_organization"))
    )
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )
    respx.get(f"{BASE}/users").mock(
        return_value=httpx.Response(users_status, json=load("graph_users"))
    )


@respx.mock
def test_check_entra_ok(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.build_graph_client", _fake_client)
    _mock_graph()
    assert cli_main(["check-entra", "cg"]) == 0
    out = capsys.readouterr().out
    assert "Organisation: Beispiel GmbH (2 Domänen)" in out
    assert "SKUs: 3" in out
    assert "signInActivity: verfügbar" in out


@respx.mock
def test_check_entra_reports_missing_sign_in(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.build_graph_client", _fake_client)
    _mock_graph(users_status=403)
    assert cli_main(["check-entra", "cvh"]) == 0
    assert "NICHT verfügbar" in capsys.readouterr().out


def test_check_entra_unknown_tenant(env: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["check-entra", "gibt-es-nicht"]) == 2
    assert "unbekannt" in capsys.readouterr().out


def test_check_entra_missing_secret_is_reported(
    env: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["check-entra", "cg"]) == 1  # Secret-Datei fehlt
    assert "FEHLER" in capsys.readouterr().out


@respx.mock
def test_check_entra_graph_error(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.build_graph_client", _fake_client)
    respx.get(f"{BASE}/organization").mock(
        return_value=httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
    )
    assert cli_main(["check-entra", "cg"]) == 1
    assert "status=401" in capsys.readouterr().out


@respx.mock
def test_sync_entra_success(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.core.sync.build_graph_client", _fake_client)
    _mock_graph()
    assert cli_main(["sync-entra", "cg"]) == 0
    out = capsys.readouterr().out
    assert "success" in out
    assert "Konten: 5" in out
    assert "SKUs: 3" in out


@respx.mock
def test_sync_entra_partial_prints_warning(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.core.sync.build_graph_client", _fake_client)
    respx.get(f"{BASE}/subscribedSkus").mock(
        return_value=httpx.Response(200, json=load("graph_skus"))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "signInActivity" in request.url.params["$select"]:
            return httpx.Response(403, json={})
        return httpx.Response(200, json=load("graph_users"))

    respx.get(f"{BASE}/users").mock(side_effect=handler)
    assert cli_main(["sync-entra", "cg"]) == 0
    assert "Warnungen: sign_in_activity_unavailable" in capsys.readouterr().out


def test_sync_entra_failure_exit_code(env: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["sync-entra", "cg"]) == 1  # Secret fehlt -> Lauf failed
    out = capsys.readouterr().out
    assert "failed" in out
    assert "Fehler: ConfigError" in out


def test_sync_entra_unknown_tenant(env: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["sync-entra", "nein"]) == 2
    assert "FEHLER" in capsys.readouterr().out


def test_graph_error_str_has_no_details() -> None:
    assert str(GraphError(403, "X")) == "Graph-Fehler: status=403 code=X"


def test_refresh_identities_unknown_tenant(
    env: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["refresh-identities", "gibt-es-nicht"]) == 2
    assert "unbekannt" in capsys.readouterr().out


def test_refresh_identities_without_runs(env: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["refresh-identities", "cg"]) == 1
    assert "nichts berechnet" in capsys.readouterr().out


@respx.mock
def test_refresh_identities_after_entra_sync(
    env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.core.sync.build_graph_client", _fake_client)
    _mock_graph()
    assert cli_main(["sync-entra", "cg"]) == 0
    capsys.readouterr()
    assert cli_main(["refresh-identities", "cg"]) == 0
    out = capsys.readouterr().out
    assert "Identitäten: " in out
    assert "Geflaggt: " in out
