"""Anzeigetexte für Codes aus der Logik (Status, Kontoart, Warnungen) und Zeitformat.

Zeiten sind intern UTC und erscheinen in der konfigurierten Zeitzone (``KW_TIMEZONE``) als
``TT.MM.JJJJ HH:MM``; die Zeitzone steht im Seitenfuß.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

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

SOURCE_LABELS = {"entra": "Entra", "ad": "AD", "both": "Entra + AD"}

RUN_STATUS_LABELS = {
    "running": "läuft",
    "success": "erfolgreich",
    "partial": "teilweise",
    "failed": "fehlgeschlagen",
}

WARNING_LABELS = {
    "entra_source_references_incomplete": (
        "Stabile Entra-Quellreferenzen sind unvollständig; Verzeichnis- und Konto-IDs prüfen"
    ),
    "sign_in_activity_unavailable": (
        "Entra liefert keine Anmeldedaten (Entra-ID-P1-Lizenz oder Berechtigung "
        "AuditLog.Read.All fehlt); Konten ohne AD-Anmeldung gelten als unbekannt"
    ),
    "ad_dc_unreachable": "Ein Domänencontroller war nicht erreichbar",
    "ad_dc_defective": "Ein Domänencontroller ist defekt und wird für Stammdaten nicht genutzt",
    "ad_dc_suspect": "Ein Domänencontroller weicht in der Kontenzahl von den anderen ab",
    "ad_master_data_incomplete": "Stammdaten einzelner AD-Konten sind unvollständig",
    "ad_weak_key_exception_active": "Befristete Ausnahme für schwache DC-Schlüssel ist aktiv",
    "ad_weak_key_exception_expired": "Die Ausnahme für schwache DC-Schlüssel ist abgelaufen",
    "run_failed": "Der jüngste Abruf ist fehlgeschlagen",
}

_display_tz: tzinfo = UTC


def set_display_timezone(tz: tzinfo) -> None:
    """Legt die Zeitzone der Anzeige fest (beim Start der Anwendung)."""
    global _display_tz
    _display_tz = tz


def display_timezone() -> tzinfo:
    """Die Zeitzone der Anzeige."""
    return _display_tz


def status_label(code: str | None) -> str:
    """Anzeigetext zu einem Statuscode; Unbekanntes erscheint als „-“."""
    return STATUS_LABELS.get(code or "", "-")


def account_type_label(code: str | None) -> str:
    """Anzeigetext zu einer Kontoart; Unbekanntes erscheint als „-“."""
    return ACCOUNT_TYPE_LABELS.get(code or "", "-")


def source_label(code: str | None) -> str:
    """Anzeigetext zu einer Quelle (``entra``, ``ad``, ``both``)."""
    return SOURCE_LABELS.get(code or "", "-")


def run_status_label(code: object) -> str:
    """Anzeigetext zu einem Laufstatus (auch ``RunStatus``)."""
    return RUN_STATUS_LABELS.get(str(getattr(code, "value", code)), "-")


def warning_label(code: str) -> str:
    """Anzeigetext zu einem Warncode; unbekannte Codes erscheinen unverändert (fester Code)."""
    return WARNING_LABELS.get(code, code)


def format_dt(value: datetime | None) -> str:
    """``TT.MM.JJJJ HH:MM`` in der Anzeige-Zeitzone; ``-`` ohne Wert."""
    if value is None:
        return "-"
    return value.astimezone(_display_tz).strftime("%d.%m.%Y %H:%M")
