"""Externe Sicherungen sind ohne ausdrückliche Aktivierung vollständig aus."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import SECRET_NAME_PATTERN, ConfigError, clock_time, positive_int

_SFTP = re.compile(
    r"^sftp:(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*:/[A-Za-z0-9_./-]+$"
)


@dataclass(frozen=True)
class ExternalBackupSettings:
    """Restic-Ziel und Secret-Referenzen; keine Zugangsdaten in URL oder Datenbank."""

    repository: str
    password_secret: str
    credentials_secret: str | None = None
    ssh_secret: str | None = None
    ca_file: str | None = None
    time: str = "02:45"
    timeout_seconds: int = 1800
    warn_hours: int = 36
    keep_daily: int = 14
    keep_weekly: int = 8
    keep_monthly: int = 12

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ExternalBackupSettings | None:
        """Ignoriert unvollständige Zielvorlagen solange KW_EXTERNAL_BACKUP_ENABLED=0 gilt."""
        enabled = env.get("KW_EXTERNAL_BACKUP_ENABLED", "0").strip().lower()
        if enabled in ("", "0", "false"):
            return None
        if enabled not in ("1", "true"):
            raise ConfigError("KW_EXTERNAL_BACKUP_ENABLED muss 0 oder 1 sein")
        repo = env.get("KW_EXTERNAL_BACKUP_REPOSITORY", "").strip()
        try:
            if repo.startswith("sftp:"):
                valid = bool(_SFTP.fullmatch(repo)) and ".." not in repo.split(":")[-1].split("/")
            elif repo.startswith(("rest:https://", "s3:https://")):
                url = urlsplit(repo.split(":", 1)[1])
                valid = bool(url.hostname) and not (
                    url.username or url.password or url.query or url.fragment
                )
                valid = valid and url.port != 0
            else:
                valid = False
            if not valid or any(c.isspace() or ord(c) < 32 for c in repo):
                raise ValueError
        except ValueError:
            raise ConfigError(
                "KW_EXTERNAL_BACKUP_REPOSITORY benötigt ein HTTPS-REST-/S3- "
                "oder SFTP-Ziel ohne Zugangsdaten"
            ) from None

        def secret(key: str, required: bool = False) -> str | None:
            value = env.get(key, "").strip()
            if not value and not required:
                return None
            if not SECRET_NAME_PATTERN.fullmatch(value):
                raise ConfigError(f"{key} fehlt oder ist ungültig")
            return value

        password = secret("KW_EXTERNAL_BACKUP_PASSWORD_SECRET", required=True)
        ssh = secret("KW_EXTERNAL_BACKUP_SSH_SECRET", required=repo.startswith("sftp:"))
        ca = env.get("KW_EXTERNAL_BACKUP_CA_FILE", "").strip() or None
        if ca and (
            not re.fullmatch(r"[A-Za-z0-9_./-]+", ca) or ca.startswith("/") or ".." in ca.split("/")
        ):
            raise ConfigError(
                "KW_EXTERNAL_BACKUP_CA_FILE muss relativ zum Konfigurationsordner sein"
            )
        timeout = positive_int(env, "KW_EXTERNAL_BACKUP_TIMEOUT_SECONDS", "1800")
        if timeout > 7200:
            raise ConfigError("KW_EXTERNAL_BACKUP_TIMEOUT_SECONDS darf höchstens 7200 sein")
        return cls(
            repository=repo,
            password_secret=password or "",
            credentials_secret=secret("KW_EXTERNAL_BACKUP_CREDENTIALS_SECRET"),
            ssh_secret=ssh,
            ca_file=ca,
            time=clock_time(env, "KW_EXTERNAL_BACKUP_TIME", "02:45"),
            timeout_seconds=timeout,
            warn_hours=positive_int(env, "KW_EXTERNAL_BACKUP_WARN_HOURS", "36"),
            keep_daily=positive_int(env, "KW_EXTERNAL_BACKUP_KEEP_DAILY", "14"),
            keep_weekly=positive_int(env, "KW_EXTERNAL_BACKUP_KEEP_WEEKLY", "8"),
            keep_monthly=positive_int(env, "KW_EXTERNAL_BACKUP_KEEP_MONTHLY", "12"),
        )
