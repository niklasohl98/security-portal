"""Spalten des Exports und Aufbereitung der Werte (gemeinsam für CSV und XLSX).

Der Export enthält immer die aktuelle Filteransicht (``core.accounts``). Zeiten erscheinen in der
Anzeige-Zeitzone als ``TT.MM.JJJJ HH:MM``. Texte, die eine Tabellenkalkulation als Formel lesen
würde (Anfang ``=``, ``+``, ``-``, ``@``, Tab, Zeilenumbruch), bekommen ein ``'`` vorangestellt
(CSV-/Formel-Injection); der Wert bleibt lesbar, wird aber nie ausgeführt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo

from app.core.accounts import AccountRow
from app.web.labels import account_type_label, source_label, status_label

DATE_FORMAT = "%d.%m.%Y %H:%M"
EXCEL_DATE_FORMAT = "DD.MM.YYYY HH:MM"
_DANGEROUS_START = ("=", "+", "-", "@", "\t", "\r", "\n")

Value = str | int | datetime | None


def safe_text(value: str) -> str:
    """Entschärft Text, der als Formel gelesen würde, durch ein vorangestelltes ``'``."""
    return "'" + value if value.startswith(_DANGEROUS_START) else value


def format_date(value: datetime | None, tz: tzinfo) -> str:
    """``TT.MM.JJJJ HH:MM`` in ``tz``; leer ohne Wert."""
    return "" if value is None else value.astimezone(tz).strftime(DATE_FORMAT)


def local_naive(value: datetime | None, tz: tzinfo) -> datetime | None:
    """Zeitpunkt in ``tz`` ohne Zeitzone (Excel kennt keine Zeitzonen in Zellen)."""
    return None if value is None else value.astimezone(tz).replace(tzinfo=None)


@dataclass(frozen=True)
class Column:
    """Eine Spalte: Überschrift, Wert je Konto, Breite (Zeichen) und ob es ein Datum ist."""

    header: str
    value: Callable[[AccountRow], str | int | datetime | None]
    width: int = 18
    is_date: bool = False


def _earliest(row: AccountRow) -> datetime | None:
    found = [
        d
        for d in (
            row.entra.created_at if row.entra else None,
            row.ad.when_created if row.ad else None,
        )
        if d
    ]
    return min(found) if found else None


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


COLUMNS: tuple[Column, ...] = (
    Column("Mandant", lambda r: r.tenant_name, 14),
    Column("Name", lambda r: r.display_name or "", 26),
    Column("Konto (UPN)", lambda r: r.upn or "", 32),
    Column("Anmeldename", lambda r: r.sam_account_name or "", 18),
    Column("E-Mail", lambda r: r.mail or "", 30),
    Column("Kontoart", lambda r: account_type_label(r.account_type) if r.account_type else "", 22),
    Column("Status", lambda r: status_label(r.status) if r.status else "", 20),
    Column("Geflaggt", lambda r: "Ja" if r.flagged else "Nein", 10),
    Column("Letzte Anmeldung", lambda r: r.last_login_at, 18, True),
    Column("Tage seit Anmeldung", lambda r: r.days_since_login, 12),
    Column(
        "Letzte Anmeldung Entra",
        lambda r: _latest(
            r.entra.last_successful_sign_in if r.entra else None,
            r.entra.last_non_interactive_sign_in if r.entra else None,
        ),
        18,
        True,
    ),
    Column(
        "Letzte Anmeldung AD",
        lambda r: _latest(
            r.ad.last_logon if r.ad else None, r.ad.last_logon_timestamp if r.ad else None
        ),
        18,
        True,
    ),
    Column("Quelle", lambda r: source_label(r.source), 12),
    Column("Lizenzen", lambda r: ", ".join(lic.name for lic in r.paid_licenses), 36),
    Column("Empfehlung", lambda r: r.recommendation or "", 40),
    Column("Ausnahme bis", lambda r: r.exception_until, 18, True),
    Column("Erstellt am", _earliest, 18, True),
)

HEADERS = tuple(c.header for c in COLUMNS)


def text_row(row: AccountRow, tz: tzinfo) -> list[str]:
    """Alle Werte einer Zeile als Text (für CSV); Zahlen bleiben Ziffern, Datumswerte formatiert."""
    out: list[str] = []
    for col in COLUMNS:
        value = col.value(row)
        if isinstance(value, datetime):
            out.append(format_date(value, tz))
        elif isinstance(value, int):
            out.append(str(value))
        else:
            out.append(safe_text(value or ""))
    return out
