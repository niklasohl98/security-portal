from __future__ import annotations

import json
import sys
import tarfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.orm import Session, sessionmaker

from app.backup import restic
from app.backup.archive import ExternalBackupError, create_bundle, unpack_bundle
from app.backup.service import execute
from app.config import Settings
from tests.conftest import EXAMPLE_TENANTS
from tests.identity_fakes import NOW
from tests.test_certificates_server import _cert
from tests.test_certificates_server import key as key
from tests.test_external_backup import external_settings as external_settings


def test_complete_recovery_package_includes_public_configuration_but_no_keys(
    external_settings: Settings,
    factory: sessionmaker[Session],
    key: rsa.RSAPrivateKey,
    tmp_path: Path,
) -> None:
    public = _cert(key, NOW - timedelta(days=1), NOW + timedelta(days=60))
    config = external_settings.tenants_file.parent
    tenants = yaml.safe_load(EXAMPLE_TENANTS.read_text())
    for tenant in tenants["tenants"]:
        if tenant.get("ad"):
            tenant["ad"]["ca_file"] = f"ca/{tenant['slug']}.pem"
    external_settings.tenants_file.write_text(yaml.safe_dump(tenants))
    for tenant in tenants["tenants"]:
        relative = tenant.get("ad", {}).get("ca_file")
        if relative:
            target = config / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(public)
    extra_ca = config / "ca" / "external.pem"
    extra_ca.parent.mkdir(parents=True, exist_ok=True)
    extra_ca.write_bytes(public)
    (config / "backup_known_hosts").write_text("backup.example ssh-ed25519 synthetic-key\n")
    inventory = config / "certificates.yaml"
    inventory.write_text("certificates: [{label: Entra-Mail, file: ca/external.pem}]\n")
    external_settings.sku_names_file.write_text("skus: []\n")
    cfg = external_settings.external_backup
    assert cfg is not None
    cfg = replace(cfg, ca_file="ca/external.pem", ssh_secret="private_ssh")
    (external_settings.secrets_dir / "private_ssh").write_text("synthetic-private-key-marker")
    settings = replace(external_settings, certificates_file=inventory, external_backup=cfg)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = create_bundle(settings, workspace)
    assert b"synthetic-private-key-marker" not in bundle.read_bytes()
    target = tmp_path / "restored"
    unpack_bundle(bundle, target)
    assert (target / "config" / "ca" / "external.pem").read_bytes() == public
    assert (target / "config" / "certificates.yaml").read_bytes() == inventory.read_bytes()
    assert (target / "config" / "sku_names.yaml").is_file()
    assert (target / "config" / "backup_known_hosts").is_file()
    assert not (target / "private_ssh").exists()
    assert not (target / "backup_password").exists()


def test_incomplete_or_corrupt_recovery_package_never_counts_as_a_restore(
    external_settings: Settings,
    factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_settings.tenants_file.unlink()
    with pytest.raises(ExternalBackupError, match="bundle"):
        create_bundle(external_settings, workspace)
    invalid = tmp_path / "invalid.tar"
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"format": 99, "database": "database.db"}')
    with tarfile.open(invalid, "w") as archive:
        archive.add(manifest, arcname="manifest.json")
    target = tmp_path / "restored"
    with pytest.raises(ExternalBackupError, match="restore"):
        unpack_bundle(invalid, target)
    assert not target.exists()


def test_sftp_uses_explicit_host_verification_and_never_passes_secret_values_in_arguments(
    external_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = external_settings.external_backup
    assert cfg is not None
    cfg = replace(cfg, ssh_secret="ssh_key")
    with pytest.raises(ExternalBackupError, match="secret"):
        restic.run_restic(external_settings, cfg, ["init"])
    (external_settings.secrets_dir / "ssh_key").write_text("synthetic-private-key-value")
    with pytest.raises(ExternalBackupError, match="ssh_hosts"):
        restic.run_restic(external_settings, cfg, ["init"])
    (external_settings.tenants_file.parent / "backup_known_hosts").write_text("synthetic-host-key")
    captured = tmp_path / "argv.json"
    executable = tmp_path / "capture-restic"
    executable.write_text(
        f"#!{sys.executable}\nimport pathlib,json,sys\n"
        f"pathlib.Path({str(captured)!r}).write_text(json.dumps(sys.argv))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setattr(restic, "RESTIC_BINARY", str(executable))
    restic.run_restic(external_settings, cfg, ["init"])
    argv = json.loads(captured.read_text())
    assert "StrictHostKeyChecking=yes" in " ".join(argv)
    assert "BatchMode=yes" in " ".join(argv)
    assert "IdentitiesOnly=yes" in " ".join(argv)
    assert "synthetic-private-key-value" not in " ".join(argv)
    credentials = external_settings.secrets_dir / "credentials"
    credentials.write_text("# comment\n\nAWS_DEFAULT_REGION=synthetic-region\n")
    ca = external_settings.tenants_file.parent / "ca.pem"
    ca.write_text("synthetic-public-ca")
    env = restic.environment(
        external_settings, replace(cfg, credentials_secret="credentials", ca_file="ca.pem")
    )
    assert env["RESTIC_CACERT"] == str(ca)
    assert env["AWS_DEFAULT_REGION"] == "synthetic-region"


def test_parallel_external_actions_are_refused_and_release_the_lock_after_io_failure(
    external_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.backup import service

    calls: list[list[str]] = []
    monkeypatch.setattr(service, "run_restic", lambda _s, _c, args: calls.append(args))
    assert service._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ExternalBackupError, match="busy"):
            execute(external_settings, "init")
    finally:
        service._LOCK.release()
    assert calls == []
    blocked = external_settings.data_dir / "not-a-directory"
    blocked.write_text("existing file")
    with pytest.raises(ExternalBackupError, match="io"):
        execute(replace(external_settings, data_dir=blocked), "backup")
    assert blocked.read_text() == "existing file"
    execute(external_settings, "check")
    assert calls == [["check", "--read-data"]]
