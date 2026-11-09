from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tarfile
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.backup import restic
from app.backup.archive import ExternalBackupError, create_bundle, unpack_bundle
from app.backup.service import execute, external_status, run_external_backup
from app.backup.settings import ExternalBackupSettings
from app.cli import main as cli_main
from app.config import ConfigError, Settings
from app.core.backup import restore_backup, verify_backup
from app.core.scheduler import EXTERNAL_BACKUP_JOB_ID, create_scheduler
from app.db.models import AppUser, AuditLog
from app.db.session import create_db_engine, make_session_factory
from tests.identity_fakes import NOW


def _env(**changes: str) -> dict[str, str]:
    return {
        "KW_EXTERNAL_BACKUP_ENABLED": "1",
        "KW_EXTERNAL_BACKUP_REPOSITORY": "rest:https://backup.example/kw/",
        "KW_EXTERNAL_BACKUP_PASSWORD_SECRET": "backup_password",
        **changes,
    }


@pytest.fixture
def external_settings(settings: Settings, tmp_path: Path, frozen_clock: object) -> Settings:
    config = tmp_path / "config"
    config.mkdir()
    data = yaml.safe_load(settings.tenants_file.read_text())
    for tenant in data["tenants"]:
        tenant.pop("ad", None)  # isolierter Test benötigt keine echten CA-/DC-Dateien
    tenants = config / "tenants.yaml"
    tenants.write_text(yaml.safe_dump(data))
    settings.secrets_dir.mkdir()
    (settings.secrets_dir / "backup_password").write_text("synthetisches-repository-passwort")
    return replace(
        settings,
        tenants_file=tenants,
        external_backup=ExternalBackupSettings(str(tmp_path / "repository"), "backup_password"),
    )


def test_external_backup_is_opt_in_and_disabled_touches_nothing(
    settings: Settings,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert Settings.from_env({}).external_backup is None
    assert ExternalBackupSettings.from_env(_env(KW_EXTERNAL_BACKUP_ENABLED="0")) is None
    with pytest.raises(ExternalBackupError, match="disabled"):
        execute(settings, "init")
    assert not run_external_backup(factory, settings)
    with factory() as session:
        assert external_status(session, settings).state == "disabled"
        assert not list(session.scalars(select(AuditLog)))
    monkeypatch.setenv("KW_EXTERNAL_BACKUP_ENABLED", "0")
    assert cli_main(["external-backup-init"]) == 2
    assert "deaktiviert" in capsys.readouterr().out


@pytest.mark.parametrize(
    "repo",
    [
        "rest:https://backup.example/",
        "s3:https://s3.example/bucket/",
        "sftp:backup@nas.example:/srv/kw",
    ],
)
def test_external_settings_accept_encrypted_transports(repo: str) -> None:
    cfg = ExternalBackupSettings.from_env(
        _env(
            KW_EXTERNAL_BACKUP_REPOSITORY=repo,
            KW_EXTERNAL_BACKUP_SSH_SECRET="backup_ssh",
            KW_EXTERNAL_BACKUP_CA_FILE="ca/backup.pem",
        )
    )
    assert cfg is not None
    assert cfg.repository == repo


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("KW_EXTERNAL_BACKUP_ENABLED", "maybe"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "/local/path"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "rest:http://backup.example/"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "s3:https://u:secret@backup.example/"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "sftp:-oProxyCommand=bad:/kw"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "sftp:nas:/../kw"),
        ("KW_EXTERNAL_BACKUP_REPOSITORY", "rest:https://[bad"),
        ("KW_EXTERNAL_BACKUP_PASSWORD_SECRET", "../password"),
        ("KW_EXTERNAL_BACKUP_TIMEOUT_SECONDS", "7201"),
        ("KW_EXTERNAL_BACKUP_TIME", "25:00"),
        ("KW_EXTERNAL_BACKUP_CA_FILE", "../ca.pem"),
    ],
)
def test_external_settings_reject_unsafe_configuration(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match=key) as exc:
        ExternalBackupSettings.from_env(_env(**{key: value}))
    assert value not in str(exc.value)


def test_external_job_only_exists_when_enabled(
    external_settings: Settings,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    assert create_scheduler(factory, settings).get_job(EXTERNAL_BACKUP_JOB_ID) is None
    job = create_scheduler(factory, external_settings).get_job(EXTERNAL_BACKUP_JOB_ID)
    assert job is not None
    assert job.func is run_external_backup
    assert job.max_instances == 1
    assert job.coalesce


def test_credentials_are_allowlisted_and_not_echoed(external_settings: Settings) -> None:
    cfg = external_settings.external_backup
    assert cfg is not None
    secret = external_settings.secrets_dir / "credentials"
    secret.write_text("AWS_ACCESS_KEY_ID=synthetic-id\nAWS_SECRET_ACCESS_KEY=synthetic-key\n")
    cfg = replace(cfg, credentials_secret=secret.name)
    env = restic.environment(external_settings, cfg)
    assert env["AWS_SECRET_ACCESS_KEY"] == "synthetic-key"
    assert env["RESTIC_PASSWORD_FILE"].endswith("/backup_password")
    assert "RESTIC_PASSWORD" not in env
    secret.write_text("RESTIC_PASSWORD_COMMAND=arbitrary-command")
    with pytest.raises(ExternalBackupError, match="credentials") as exc:
        restic.environment(external_settings, cfg)
    assert "arbitrary-command" not in str(exc.value)
    with pytest.raises(ExternalBackupError, match="secret"):
        restic.environment(external_settings, replace(cfg, password_secret="missing"))


@pytest.mark.parametrize("member_name", ["../escape", "/escape", "unrelated.txt"])
def test_archive_rejects_traversal_and_unexpected_files(tmp_path: Path, member_name: str) -> None:
    bundle = tmp_path / "bundle.tar"
    with tarfile.open(bundle, "w") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    target = tmp_path / "target"
    with pytest.raises(ExternalBackupError, match="restore"):
        unpack_bundle(bundle, target)
    assert not target.exists()
    assert not (tmp_path / "escape").exists()


def test_archive_rejects_links_and_never_removes_existing_targets(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar"
    with tarfile.open(bundle, "w") as archive:
        member = tarfile.TarInfo("config/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    target = tmp_path / "target"
    with pytest.raises(ExternalBackupError):
        unpack_bundle(bundle, target)
    target.mkdir()
    marker = target / "keep"
    marker.write_text("must stay")
    with pytest.raises(ExternalBackupError):
        unpack_bundle(bundle, target)
    assert marker.read_text() == "must stay"


def test_bundle_rejects_private_keys_in_configuration(
    external_settings: Settings,
    factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_settings.tenants_file.write_text("-----BEGIN PRIVATE KEY-----")
    with pytest.raises(ExternalBackupError, match="config"):
        create_bundle(external_settings, workspace)


def test_status_errors_are_audited_and_target_specific(
    external_settings: Settings,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.backup.service.execute", lambda *_a, **_kw: None)
    assert run_external_backup(factory, external_settings)
    for action in ("check", "restore"):
        assert run_external_backup(factory, external_settings, action=action)
    with factory() as session:
        status = external_status(session, external_settings, NOW)
        assert status.state == "ok"
        assert status.last_check_at == NOW
        assert status.last_restore_at == NOW
        assert (
            external_status(session, external_settings, NOW + timedelta(hours=37)).state == "stale"
        )
        cfg = external_settings.external_backup
        assert cfg is not None
        assert (
            external_status(
                session,
                replace(external_settings, external_backup=replace(cfg, repository="different")),
            ).state
            == "never"
        )

    def failure(*_a: object, **_kw: object) -> None:
        raise ExternalBackupError("timeout")

    monkeypatch.setattr("app.backup.service.execute", failure)
    assert not run_external_backup(factory, external_settings)
    with factory() as session:
        assert external_status(session, external_settings, NOW).state == "failed"
        assert all(
            "repository" not in (entry.detail or "") for entry in session.scalars(select(AuditLog))
        )


def test_real_encrypted_repository_roundtrip_preserves_users_and_configuration(
    external_settings: Settings,
    factory: sessionmaker[Session],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = os.environ.get("KW_TEST_RESTIC_BINARY") or shutil.which("restic")
    if not binary:
        pytest.skip("Restic-Binary für echten verschlüsselten Roundtrip nicht vorhanden")
    monkeypatch.setattr(restic, "RESTIC_BINARY", binary)
    with factory.begin() as session:
        session.add(
            AppUser(
                username="LocalTest",
                username_key="localtest",
                password_hash="synthetic-hash",
                must_change_password=True,
                is_active=True,
                is_admin=True,
            )
        )
    assert run_external_backup(factory, external_settings, action="init")
    assert run_external_backup(factory, external_settings)
    assert run_external_backup(factory, external_settings, action="check")
    target = tmp_path / "restored"
    assert run_external_backup(factory, external_settings, action="restore", target=target)
    verify_backup(target / "database.db")
    assert json.loads((target / "manifest.json").read_text())["secrets_included"] is False
    assert (
        target / "config" / "tenants.yaml"
    ).read_bytes() == external_settings.tenants_file.read_bytes()
    assert not list(target.rglob("*password*"))
    recovered_url = f"sqlite:///{tmp_path / 'recovered.db'}"
    restore_backup(target / "database.db", recovered_url)
    engine = create_db_engine(recovered_url)
    with make_session_factory(engine)() as session:
        user = session.scalars(select(AppUser).where(AppUser.username == "LocalTest")).one()
        assert user.password_hash == "synthetic-hash"
        assert user.must_change_password
    engine.dispose()
    assert not run_external_backup(factory, external_settings, action="prune")
    assert run_external_backup(factory, external_settings, action="prune", confirm_prune=True)
    cfg = external_settings.external_backup
    assert cfg is not None
    (external_settings.secrets_dir / "wrong").write_text("different-repository-password")
    with pytest.raises(ExternalBackupError, match="restic"):
        restic.run_restic(external_settings, replace(cfg, password_secret="wrong"), ["check"])


def test_external_backup_ui_is_disabled_by_default(site: TestClient) -> None:
    text = site.get("/runs").text
    assert "Externe verschlüsselte Sicherung" in text
    assert "Deaktiviert" in text
    assert "Extern sichern" not in text


def test_restic_timeout_does_not_leak_output_or_leave_a_running_child(
    external_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = tmp_path / "slow-restic"
    marker = tmp_path / "child.pid"
    script.write_text(
        f"#!{sys.executable}\nimport pathlib, subprocess, time\n"
        "child = subprocess.Popen(['/bin/sleep', '60'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
        "print('synthetic-sensitive-output', flush=True)\ntime.sleep(60)\n"
    )
    script.chmod(0o700)
    monkeypatch.setattr(restic, "RESTIC_BINARY", str(script))
    cfg = external_settings.external_backup
    assert cfg is not None
    started = time.monotonic()
    with pytest.raises(ExternalBackupError, match="timeout"):
        restic.run_restic(external_settings, replace(cfg, timeout_seconds=1), ["init"])
    assert time.monotonic() - started < 10
    assert "synthetic-sensitive-output" not in capsys.readouterr().out
    child_stat = Path(f"/proc/{marker.read_text()}/stat")
    assert not child_stat.exists() or child_stat.read_text().split()[2] == "Z"
    monkeypatch.setattr(restic, "RESTIC_BINARY", "/nonexistent/restic")
    with pytest.raises(ExternalBackupError, match="executable"):
        restic.run_restic(external_settings, cfg, ["init"])


def test_external_cli_dispatch_and_guardrails(
    external_settings: Settings,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Settings, "from_env", lambda: external_settings)
    monkeypatch.setattr("app.backup.service.execute", lambda *_a, **_kw: None)
    assert cli_main(["external-backup-run"]) == 0
    with pytest.raises(ExternalBackupError, match="action"):
        execute(external_settings, "unknown")


def test_existing_restore_target_and_invalid_snapshot_are_refused(
    external_settings: Settings,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep"
    marker.write_text("unchanged")
    with pytest.raises(ExternalBackupError, match="target"):
        execute(external_settings, "restore", target=existing)
    assert marker.read_text() == "unchanged"
    with pytest.raises(ExternalBackupError, match="snapshot"):
        execute(external_settings, "restore", target=tmp_path / "new", snapshot="foreign:/path")
