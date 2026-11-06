"""SMTP-Einstellungen aus ``KW_SMTP_*`` und ``KW_REPORT_*``.

Ohne ``KW_SMTP_HOST`` ist der Mailversand aus (``MailSettings.from_env`` gibt ``None`` zurück).
Das Passwort steht nur als *Name* eines Docker-Secrets in der Umgebung, nie im Klartext. Die
Verbindung ist immer verschlüsselt (``starttls`` oder ``ssl``), das Zertifikat wird immer
geprüft; unverschlüsseltes SMTP gibt es nicht.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config import EMAIL_PATTERN, SECRET_NAME_PATTERN, ConfigError, clock_time, positive_int

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
_SECURITY = ("starttls", "ssl")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABELS = {
    "mon": "Montag",
    "tue": "Dienstag",
    "wed": "Mittwoch",
    "thu": "Donnerstag",
    "fri": "Freitag",
    "sat": "Samstag",
    "sun": "Sonntag",
}


@dataclass(frozen=True)
class MailSettings:
    """Mailversand und Zeitpunkt des Wochenberichts.

    Attributes:
        host: SMTP-Server.
        port: Port (Standard 587 bei ``starttls``, 465 bei ``ssl``).
        security: ``starttls`` oder ``ssl``.
        sender: Absenderadresse.
        user: Benutzername der Anmeldung; ``None`` = ohne Anmeldung (Relay im internen Netz).
        password_secret: Name des Docker-Secrets mit dem Passwort.
        ca_file: Optionale CA-Datei (PEM) zusätzlich zum Systemspeicher, z. B. interne CA.
        report_weekday: Wochentag des Berichts (``mon`` bis ``sun``).
        report_time: Uhrzeit ``HH:MM`` in der konfigurierten Zeitzone.
    """

    host: str
    port: int
    security: str
    sender: str
    user: str | None = None
    password_secret: str | None = None
    ca_file: Path | None = None
    report_weekday: str = "mon"
    report_time: str = "07:30"

    @property
    def report_hour_minute(self) -> tuple[int, int]:
        """Stunde und Minute des Wochenberichts."""
        hour, minute = self.report_time.split(":")
        return int(hour), int(minute)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> MailSettings | None:
        """Liest die Einstellungen; ``None``, wenn ``KW_SMTP_HOST`` fehlt oder leer ist.

        Raises:
            ConfigError: Ungültiger Wert (die Meldung nennt nur den Namen der Variable).
        """
        env = {k: v.strip() for k, v in env.items() if v.strip()}  # leer = nicht gesetzt (Compose)
        host = env.get("KW_SMTP_HOST", "")
        if not host:
            return None
        if not _HOST.fullmatch(host):
            raise ConfigError("KW_SMTP_HOST ist kein gültiger Hostname")
        security = env.get("KW_SMTP_SECURITY", "starttls").lower()
        if security not in _SECURITY:
            raise ConfigError("KW_SMTP_SECURITY muss starttls oder ssl sein")
        port = positive_int(env, "KW_SMTP_PORT", "465" if security == "ssl" else "587")
        if port > 65535:
            raise ConfigError("KW_SMTP_PORT muss höchstens 65535 sein")
        sender = env.get("KW_MAIL_FROM", "")
        if not EMAIL_PATTERN.fullmatch(sender):
            raise ConfigError("KW_MAIL_FROM muss eine Mailadresse sein")
        user = env.get("KW_SMTP_USER") or None
        secret = env.get("KW_SMTP_PASSWORD_SECRET") or None
        if secret is not None and not SECRET_NAME_PATTERN.fullmatch(secret):
            raise ConfigError("KW_SMTP_PASSWORD_SECRET ist kein gültiger Secret-Name")
        if (user is None) != (secret is None):
            raise ConfigError("KW_SMTP_USER und KW_SMTP_PASSWORD_SECRET gehören zusammen")
        ca = env.get("KW_SMTP_CA_FILE", "")
        weekday = env.get("KW_REPORT_WEEKDAY", "mon").lower()
        if weekday not in WEEKDAYS:
            raise ConfigError("KW_REPORT_WEEKDAY muss mon, tue, wed, thu, fri, sat oder sun sein")
        return cls(
            host=host,
            port=port,
            security=security,
            sender=sender,
            user=user,
            password_secret=secret,
            ca_file=Path(ca) if ca else None,
            report_weekday=weekday,
            report_time=clock_time(env, "KW_REPORT_TIME", "07:30"),
        )
