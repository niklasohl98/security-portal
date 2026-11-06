"""Einstellungen des Mailversands über Exchange Online (Microsoft Graph) aus ``KW_MAIL_*``.

Ohne ``KW_MAIL_FROM`` ist der Mailversand aus (``MailSettings.from_env`` gibt ``None`` zurück).
Die Anmeldung nutzt eine eigene App-Registrierung mit Zertifikat (wie der Entra-Abruf); der private
Schlüssel liegt als Docker-Secret, in der Umgebung steht nur dessen *Name*. Gesendet wird als
Postfach ``KW_MAIL_FROM``; die Berechtigung dafür gehört in Exchange auf genau dieses Postfach
begrenzt (``docs/mail-setup.md``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import (
    EMAIL_PATTERN,
    SECRET_NAME_PATTERN,
    THUMBPRINT_PATTERN,
    ConfigError,
    clock_time,
)

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


def _guid(env: Mapping[str, str], name: str) -> str:
    try:
        return str(uuid.UUID(env.get(name, "")))
    except ValueError:
        raise ConfigError(f"{name} muss eine GUID sein") from None


@dataclass(frozen=True)
class MailSettings:
    """Mailversand und Zeitpunkt des Wochenberichts.

    Attributes:
        tenant_id: Verzeichnis-ID (Tenant) des Exchange-Online-Mandanten, aus dem gesendet wird.
        client_id: Anwendungs-ID der App-Registrierung.
        certificate_secret: Name des Docker-Secrets mit dem privaten Schlüssel (PEM).
        certificate_thumbprint: SHA-1-Fingerabdruck des Zertifikats (40 Hex-Zeichen, groß).
        sender: Postfach, als das gesendet wird (Adresse oder UPN).
        report_weekday: Wochentag des Berichts (``mon`` bis ``sun``).
        report_time: Uhrzeit ``HH:MM`` in der konfigurierten Zeitzone.
    """

    tenant_id: str
    client_id: str
    certificate_secret: str
    certificate_thumbprint: str
    sender: str
    report_weekday: str = "mon"
    report_time: str = "07:30"

    @property
    def report_hour_minute(self) -> tuple[int, int]:
        """Stunde und Minute des Wochenberichts."""
        hour, minute = self.report_time.split(":")
        return int(hour), int(minute)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> MailSettings | None:
        """Liest die Einstellungen; ``None``, wenn ``KW_MAIL_FROM`` fehlt oder leer ist.

        Leere Werte zählen als nicht gesetzt (Compose reicht sie durch).

        Raises:
            ConfigError: Ungültiger oder fehlender Wert (die Meldung nennt nur den Namen).
        """
        env = {k: v.strip() for k, v in env.items() if v.strip()}
        sender = env.get("KW_MAIL_FROM", "")
        if not sender:
            return None
        if not EMAIL_PATTERN.fullmatch(sender):
            raise ConfigError("KW_MAIL_FROM muss eine Mailadresse sein")
        secret = env.get("KW_MAIL_CERT_SECRET", "")
        if not SECRET_NAME_PATTERN.fullmatch(secret):
            raise ConfigError("KW_MAIL_CERT_SECRET fehlt oder ist kein gültiger Secret-Name")
        thumbprint = env.get("KW_MAIL_CERT_THUMBPRINT", "")
        if not THUMBPRINT_PATTERN.fullmatch(thumbprint):
            raise ConfigError("KW_MAIL_CERT_THUMBPRINT muss 40 Hex-Zeichen haben")
        weekday = env.get("KW_REPORT_WEEKDAY", "mon").lower()
        if weekday not in WEEKDAYS:
            raise ConfigError("KW_REPORT_WEEKDAY muss mon, tue, wed, thu, fri, sat oder sun sein")
        return cls(
            tenant_id=_guid(env, "KW_MAIL_TENANT_ID"),
            client_id=_guid(env, "KW_MAIL_CLIENT_ID"),
            certificate_secret=secret,
            certificate_thumbprint=thumbprint.upper(),
            sender=sender,
            report_weekday=weekday,
            report_time=clock_time(env, "KW_REPORT_TIME", "07:30"),
        )
