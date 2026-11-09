from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.backup.settings import ExternalBackupSettings
from app.config import Settings
from app.db.models import AppUser
from tests.web_fakes import open_session, post_form


def test_manual_external_backup_requires_admin_csrf_and_an_enabled_target(
    site: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Settings, str]] = []

    def run(_factory: object, cfg: Settings, actor: str) -> None:
        calls.append((cfg, actor))

    monkeypatch.setattr("app.web.runs.run_external_backup", run)
    assert post_form(site, "/backup/external/start").status_code == 303
    assert calls == []
    settings: Settings = site.app.state.settings  # type: ignore[attr-defined]
    enabled = replace(
        settings,
        external_backup=ExternalBackupSettings(
            "rest:https://backup.example/kw/", "backup_password"
        ),
    )
    site.app.state.settings = enabled  # type: ignore[attr-defined]
    assert post_form(site, "/backup/external/start", csrf=False).status_code == 403
    assert (
        post_form(site, "/backup/external/start", headers={"origin": "https://["}).status_code
        == 403
    )
    assert calls == []
    response = post_form(site, "/backup/external/start")
    assert (response.status_code, response.headers["location"]) == (303, "/runs")
    assert calls == [(enabled, "Tester")]
    with open_session(site) as session, session.begin():
        user = session.query(AppUser).filter_by(username="Tester").one()
        user.is_admin = False
    assert post_form(site, "/backup/external/start").status_code == 403
    assert calls == [(enabled, "Tester")]
