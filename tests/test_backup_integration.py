from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.cli import main as cli_main
from app.config import ConfigError, Settings
from app.core.backup import create_backup, list_backups
from app.core.scheduler import BACKUP_JOB_ID, create_scheduler
from app.db.models import AuditLog, Identity
from app.main import create_app
from tests.identity_fakes import NOW, get_tenant
from tests.web_fakes import open_session, post_form

# --- Konfiguration ---


def test_backup_settings_defaults_and_overrides() -> None:
    s = Settings.from_env({})
    assert (s.backup_dir, s.backup_time, s.backup_keep_days, s.backup_warn_hours) == (
        Path("/backup"),
        "02:30",
        14,
        36,
    )
    assert s.backup_hour_minute == (2, 30)
    s2 = Settings.from_env(
        {
            "KW_BACKUP_DIR": "/x/sicherung",
            "KW_BACKUP_TIME": "23:15",
            "KW_BACKUP_KEEP_DAYS": "30",
            "KW_BACKUP_WARN_HOURS": "48",
        }
    )
    assert (s2.backup_dir, s2.backup_hour_minute, s2.backup_keep_days, s2.backup_warn_hours) == (
        Path("/x/sicherung"),
        (23, 15),
        30,
        48,
    )


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"KW_BACKUP_TIME": "25:00"}, "KW_BACKUP_TIME"),
        ({"KW_BACKUP_KEEP_DAYS": "0"}, "KW_BACKUP_KEEP_DAYS"),
        ({"KW_BACKUP_KEEP_DAYS": "viele"}, "KW_BACKUP_KEEP_DAYS"),
        ({"KW_BACKUP_WARN_HOURS": "-1"}, "KW_BACKUP_WARN_HOURS"),
    ],
)
def test_invalid_backup_settings_name_only_the_variable(env: dict[str, str], name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        Settings.from_env(env)


# --- Zeitplan ---


def test_the_scheduler_has_a_separate_backup_job(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    cfg = replace(settings, backup_time="01:45", timezone="Europe/Berlin")
    job = create_scheduler(factory, cfg).get_job(BACKUP_JOB_ID)
    assert job is not None
    assert (job.max_instances, job.coalesce) == (1, True)
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("1", "45")


# --- Oberfläche ---


def _write_backup(settings: Settings, hours_old: float) -> Path:
    settings.backup_dir.mkdir(exist_ok=True)
    path = settings.backup_dir / "kontenwaechter-20261106-000000.db"
    path.write_bytes(b"x")
    ts = (NOW - timedelta(hours=hours_old)).timestamp()
    os.utime(path, (ts, ts))
    return path


def test_overview_warns_when_there_is_no_backup(site: TestClient) -> None:
    text = site.get("/").text
    assert "Es gibt noch keine Datenbanksicherung" in text
    assert 'role="alert"' in text


def test_overview_is_quiet_with_a_fresh_backup_and_warns_when_it_is_old(
    site: TestClient, settings: Settings
) -> None:
    path = _write_backup(settings, 2)
    text = site.get("/").text
    assert "Letzte Datenbanksicherung: 06.11.2026 11:00" in text
    assert "Es gibt noch keine" not in text
    _write_backup(settings, 40)  # älter als die Schwelle von 36 Stunden
    stale = site.get("/").text
    assert "älter als 36 Stunden" in stale
    assert path.exists()


def test_overview_warns_after_a_failed_backup(site: TestClient, settings: Settings) -> None:
    _write_backup(settings, 5)
    with open_session(site) as s, s.begin():
        s.add(
            AuditLog(
                tenant_id=None, at=NOW, actor="system", action="backup.failed", detail="code=io"
            )
        )
    assert "Datenbanksicherung ist fehlgeschlagen" in site.get("/").text


def test_runs_page_lists_backups_and_offers_the_button(
    site: TestClient, settings: Settings
) -> None:
    empty = site.get("/runs").text
    assert "Noch keine Sicherungen" in empty
    assert 'action="/backup/start"' in empty
    assert f'name="csrf_token" value="{site.csrf_token}"' in empty
    with open_session(site) as s, s.begin():
        s.add(Identity(tenant_id=get_tenant(s).id, key="k", display_name="x"))
    create_backup(settings.database_url, settings.backup_dir)
    page = site.get("/runs").text
    assert "kontenwaechter-" in page
    assert "regulär" in page
    assert str(settings.backup_dir) in page


def test_the_backup_button_creates_a_checked_backup_and_is_audited(
    site: TestClient, settings: Settings
) -> None:
    r = post_form(site, "/backup/start")
    assert (r.status_code, r.headers["location"]) == (303, "/runs?done=backup_ok")
    (made,) = list_backups(settings.backup_dir)
    assert made.size > 0
    assert "Sicherung angelegt und geprüft" in site.get("/runs?done=backup_ok").text
    with open_session(site) as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "backup.ok")).one()
        assert entry.actor == "Tester"


def test_the_backup_button_reports_failure(
    site: TestClient, settings: Settings, tmp_path: Path
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x", encoding="utf-8")
    site.app.state.settings = replace(settings, backup_dir=blocker / "unter")  # type: ignore[attr-defined]
    r = post_form(site, "/backup/start")
    assert r.headers["location"] == "/runs?done=backup_failed"
    assert "Sicherung ist fehlgeschlagen" in site.get("/runs?done=backup_failed").text
    with open_session(site) as s:
        assert (
            s.scalars(select(AuditLog).where(AuditLog.action == "backup.failed")).one().detail
            == "code=dir"
        )


def test_the_backup_route_needs_csrf_token_and_same_origin(
    site: TestClient, settings: Settings
) -> None:
    assert post_form(site, "/backup/start", csrf=False).status_code == 403
    assert (
        post_form(site, "/backup/start", headers={"origin": "https://boese.example"}).status_code
        == 403
    )
    assert list_backups(settings.backup_dir) == []


def test_anonymous_users_cannot_start_a_backup(anon: TestClient, settings: Settings) -> None:
    assert anon.post("/backup/start", follow_redirects=False).status_code == 401
    assert list_backups(settings.backup_dir) == []


# --- Befehlszeile ---


@pytest.fixture
def env(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    with TestClient(create_app(settings)):
        pass
    monkeypatch.setenv("KW_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("KW_BACKUP_DIR", str(settings.backup_dir))
    return settings


def test_cli_backup_list_verify_roundtrip(
    env: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["backup-list"]) == 0
    assert "Keine Sicherungen" in capsys.readouterr().out
    assert cli_main(["backup"]) == 0
    assert "Gesichert: kontenwaechter-" in capsys.readouterr().out
    assert cli_main(["backup-list"]) == 0
    listed = capsys.readouterr().out
    assert "regulär" in listed
    name = listed.split("\t")[0]
    assert cli_main(["backup-verify", str(env.backup_dir / name)]) == 0
    assert "In Ordnung" in capsys.readouterr().out


def test_cli_backup_reports_a_failure(
    env: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("KW_BACKUP_DIR", str(blocker / "unter"))
    assert cli_main(["backup"]) == 1
    assert "FEHLER" in capsys.readouterr().out


def test_cli_verify_rejects_a_bad_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "muell.db"
    bad.write_bytes(b"x" * 3000)
    assert cli_main(["backup-verify", str(bad)]) == 1
    assert "nicht in Ordnung" in capsys.readouterr().out


def test_cli_restore_needs_confirmation_and_brings_the_data_back(
    env: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    engine_factory = TestClient(create_app(env))
    with engine_factory as client, open_session(client) as s, s.begin():
        s.add(Identity(tenant_id=get_tenant(s).id, key="a", display_name="Gesichert"))
    assert cli_main(["backup"]) == 0
    name = re.search(r"Gesichert: (\S+)", capsys.readouterr().out).group(1)  # type: ignore[union-attr]
    with engine_factory as client, open_session(client) as s, s.begin():
        s.execute(delete(Identity))
    path = str(env.backup_dir / name)
    assert cli_main(["restore-backup", path]) == 2  # ohne --yes passiert nichts
    assert "--yes" in capsys.readouterr().out
    assert cli_main(["restore-backup", path, "--yes"]) == 0
    assert "Wiederhergestellt" in capsys.readouterr().out
    with TestClient(create_app(env)) as client, open_session(client) as s:
        assert [i.display_name for i in s.scalars(select(Identity))] == ["Gesichert"]


def test_cli_restore_refuses_a_bad_file(
    env: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "muell.db"
    bad.write_bytes(b"x" * 3000)
    assert cli_main(["restore-backup", str(bad), "--yes"]) == 1
    assert "Datenbank unverändert" in capsys.readouterr().out
