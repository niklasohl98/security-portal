"""Begrenzter Restic-Aufruf ohne Shell oder Ausgabe von Pfaden/Geheimnissen."""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

from app.backup.archive import ExternalBackupError
from app.backup.settings import ExternalBackupSettings
from app.config import ConfigError, Settings, read_secret, resolve_ca_file

RESTIC_BINARY = "/usr/local/bin/restic"
_CREDENTIAL_KEYS = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "RESTIC_REST_USERNAME",
        "RESTIC_REST_PASSWORD",
    }
)
_NETWORK_ENV = frozenset(
    {
        "PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SYSTEMROOT",
    }
)


def environment(settings: Settings, cfg: ExternalBackupSettings) -> dict[str, str]:
    """Nur explizite Verbindungs-/Proxywerte übernehmen, Credentials ausschließlich aus Secrets."""
    env = {key: value for key, value in os.environ.items() if key in _NETWORK_ENV}
    try:
        read_secret(cfg.password_secret, settings.secrets_dir)  # Existenz und Lesbarkeit prüfen
        env.update(
            RESTIC_REPOSITORY=cfg.repository,
            RESTIC_PASSWORD_FILE=str(settings.secrets_dir / cfg.password_secret),
        )
        if cfg.credentials_secret:
            for line in read_secret(cfg.credentials_secret, settings.secrets_dir).splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if not separator or key not in _CREDENTIAL_KEYS or not value:
                    raise ExternalBackupError("credentials")
                env[key] = value
        if cfg.ca_file:
            env["RESTIC_CACERT"] = str(resolve_ca_file(settings.tenants_file, cfg.ca_file))
    except ConfigError:
        raise ExternalBackupError("secret") from None
    return env


def run_restic(
    settings: Settings,
    cfg: ExternalBackupSettings,
    arguments: list[str],
    source: Path | None = None,
) -> None:
    """Führt genau einen zeitbegrenzten Aufruf aus; auch Teilfehler gelten als Fehlschlag."""
    args = [RESTIC_BINARY, "--no-cache"]
    if cfg.ssh_secret:
        try:
            read_secret(cfg.ssh_secret, settings.secrets_dir)
        except ConfigError:
            raise ExternalBackupError("secret") from None
        known_hosts = settings.tenants_file.parent / "backup_known_hosts"
        if not known_hosts.is_file():
            raise ExternalBackupError("ssh_hosts")
        ssh_key = settings.secrets_dir / cfg.ssh_secret
        args.extend(
            [
                "-o",
                "sftp.args=-oBatchMode=yes -oStrictHostKeyChecking=yes "
                f"-oUserKnownHostsFile={known_hosts} -oIdentityFile={ssh_key} -oIdentitiesOnly=yes",
            ]
        )
    args.extend(arguments)
    env = environment(settings, cfg)
    try:
        with (
            source.open("rb") if source else open(os.devnull, "rb") as stdin,
            subprocess.Popen(  # noqa: S603 - feste Argumente, keine Shell
                args,
                stdin=stdin,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            ) as process,
        ):
            try:
                code = process.wait(timeout=cfg.timeout_seconds)
            except subprocess.TimeoutExpired:
                # Auch einen von Restic gestarteten SSH-Prozess beenden, bevor das Paket
                # entfernt und die Aktionssperre freigegeben wird.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ExternalBackupError("timeout") from None
        if code:
            raise ExternalBackupError("restic")
    except OSError:
        raise ExternalBackupError("executable") from None
