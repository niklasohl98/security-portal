from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.config import ConfigError, Settings
from app.db.models import Tenant
from app.logging_config import JsonFormatter, configure_logging
from app.main import create_app


def test_health_ok_and_tenants_created(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        with client.app.state.session_factory() as s:  # type: ignore[attr-defined]
            slugs = [t.slug for t in s.scalars(select(Tenant).order_by(Tenant.slug))]
    assert slugs == ["cg", "cvh"]


def test_app_can_start_twice_on_same_database(settings: Settings) -> None:
    """Regression: zweiter Start (``docker compose restart``) schlug bei Migrationen fehl."""
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200


def test_health_reports_unhealthy_when_database_fails(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        client.app.state.engine.dispose()  # type: ignore[attr-defined]
        client.app.state.engine = object()  # type: ignore[attr-defined]  # connect() schlägt fehl
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unhealthy"}


def test_openapi_and_docs_are_not_exposed(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


def test_startup_fails_on_invalid_tenant_file(settings: Settings, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("tenants:\n  - slug: x\n    name: X\n", encoding="utf-8")
    broken = Settings(
        data_dir=settings.data_dir,
        database_url=settings.database_url,
        tenants_file=bad,
        secrets_dir=settings.secrets_dir,
        log_level="WARNING",
    )
    with pytest.raises(ConfigError), TestClient(create_app(broken)):
        pass


def test_cli_lists_tenants(
    settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(settings)):
        pass
    monkeypatch.setenv("KW_DATABASE_URL", settings.database_url)
    assert cli_main(["tenants"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("cg\tCG\tentra+ad\t30d\taktiv")
    assert out[1].startswith("cvh\tCVH\t")


def test_json_formatter_emits_json_without_exception_message() -> None:
    try:
        raise ValueError("passwort=geheim")
    except ValueError:
        import sys

        rec = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "Fehler %s", ("x",), sys.exc_info()
        )
    line = JsonFormatter().format(rec)
    data = json.loads(line)
    assert data["msg"] == "Fehler x"
    assert data["exc_type"] == "ValueError"
    assert "geheim" not in line


def test_configure_logging_routes_uvicorn_through_json_handler() -> None:
    configure_logging("INFO")
    handler = logging.getLogger("uvicorn.error").handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)
    assert logging.getLogger("uvicorn.error").propagate is False
    assert logging.getLogger().handlers == [handler]
