"""Anzeigetexte für Codes aus der Logik (Status, Kontoart) und Zeitformat der Oberfläche."""

from __future__ import annotations

from datetime import UTC, datetime

STATUS_LABELS = {
    "exception": "Ausnahme",
    "new": "Neu",
    "disabled": "Deaktiviert",
    "unknown": "Unbekannt (keine Anmeldedaten)",
    "never": "Nie angemeldet",
    "inactive_long": "Inaktiv (lang)",
    "inactive": "Inaktiv",
    "active": "Aktiv",
    "missing": "Verschwunden",
}

ACCOUNT_TYPE_LABELS = {
    "member_synced": "Mitglied, synchronisiert",
    "member_cloud": "Mitglied, nur Cloud",
    "member_onprem": "Mitglied, nur AD",
    "guest": "Gast",
    "admin": "Admin",
    "service": "Dienst/Sync",
    "test": "Test",
    "resource": "Postfach/Ressource",
}


def status_label(code: str | None) -> str:
    """Anzeigetext zu einem Statuscode; Unbekanntes erscheint als „-“."""
    return STATUS_LABELS.get(code or "", "-")


def account_type_label(code: str | None) -> str:
    """Anzeigetext zu einer Kontoart; Unbekanntes erscheint als „-“."""
    return ACCOUNT_TYPE_LABELS.get(code or "", "-")


def format_dt(value: datetime | None) -> str:
    """``TT.MM.JJJJ HH:MM UTC``; ``-`` ohne Wert. Zeiten sind intern immer UTC."""
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
