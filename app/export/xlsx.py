"""XLSX-Export (openpyxl): Blätter „Konten“, „Lizenzen“ und „Info“.

„Konten“ ist eine formatierte Excel-Tabelle mit Filtern und fixierter Kopfzeile; der Status ist
farbig. „Lizenzen“ zählt die nicht kostenlosen Lizenzen geflaggter Konten der Ansicht je SKU
(freiwerdende Lizenzen). „Info“ nennt Mandant, Stichtag, Schwellwerte und Filter. Datumswerte sind
echte Excel-Datumszellen in der Anzeige-Zeitzone; Text wird gegen Formel-Injection entschärft.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.core.accounts import AccountRow
from app.export.columns import COLUMNS, EXCEL_DATE_FORMAT, HEADERS, local_naive, safe_text

# Hintergrundfarben je Status (helle Töne, damit schwarze Schrift lesbar bleibt).
STATUS_FILLS = {
    "never": "F8C9C4",
    "inactive_long": "F8C9C4",
    "inactive": "FDE2B8",
    "disabled": "E0E0E0",
    "unknown": "FFF3B0",
    "exception": "C9DDF8",
    "new": "DDEBF9",
    "active": "D5EFD2",
}
SHEET_ACCOUNTS = "Konten"
SHEET_LICENSES = "Lizenzen"
SHEET_INFO = "Info"


@dataclass(frozen=True)
class TenantInfo:
    """Schwellwerte eines Mandanten für das Blatt „Info“."""

    name: str
    inactive_days: int
    long_inactive_days: int
    new_account_grace_days: int


@dataclass(frozen=True)
class ExportInfo:
    """Angaben für das Blatt „Info“."""

    created_at: datetime
    tenants: list[TenantInfo]
    filters: list[tuple[str, str]] = field(default_factory=list)


def _cell_value(value: object, tz: tzinfo) -> object:
    if isinstance(value, datetime):
        return local_naive(value, tz)
    if isinstance(value, str):
        return safe_text(value)
    return value


def _accounts_sheet(wb: Workbook, rows: list[AccountRow], tz: tzinfo) -> None:
    ws = wb.active
    assert ws is not None  # noqa: S101 - ein neues Arbeitsbuch hat immer ein aktives Blatt
    ws.title = SHEET_ACCOUNTS
    ws.append(list(HEADERS))
    for row in rows:
        ws.append([_cell_value(col.value(row), tz) for col in COLUMNS])
    status_col = HEADERS.index("Status") + 1
    for idx, col in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = col.width
        if col.is_date:
            for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=2, max_row=len(rows) + 1):
                for c in cell:
                    c.number_format = EXCEL_DATE_FORMAT
    for offset, row in enumerate(rows, start=2):
        color = STATUS_FILLS.get(row.status or "")
        if color:
            ws.cell(row=offset, column=status_col).fill = PatternFill("solid", fgColor=color)
    ws.freeze_panes = "A2"
    # Eine Tabelle braucht mindestens eine Datenzeile; ohne Konten bleibt nur die Kopfzeile.
    last_row = max(len(rows) + 1, 2)
    table = Table(displayName="Konten", ref=f"A1:{get_column_letter(len(COLUMNS))}{last_row}")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)


def _licenses_sheet(wb: Workbook, rows: list[AccountRow]) -> None:
    ws = wb.create_sheet(SHEET_LICENSES)
    ws.append(["Mandant", "Lizenz", "Konten (geflaggt)"])
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row.flagged:
            counts.update((row.tenant_name, lic.name) for lic in row.paid_licenses)
    for (tenant, name), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        ws.append([safe_text(tenant), safe_text(name), n])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 18
    ws.freeze_panes = "A2"


def _info_sheet(wb: Workbook, info: ExportInfo, count: int, tz: tzinfo) -> None:
    ws = wb.create_sheet(SHEET_INFO)
    ws.append(["Inaktive-Konten-Wächter: Export"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["Stichtag", local_naive(info.created_at, tz)])
    ws.cell(row=3, column=2).number_format = EXCEL_DATE_FORMAT
    ws.cell(row=3, column=2).alignment = Alignment(horizontal="left")
    ws.append(["Zeitzone", str(tz)])
    ws.append(["Anzahl Konten", count])
    ws.append([])
    ws.append(
        ["Mandant", "Inaktiv ab (Tage)", "Lang inaktiv ab (Tage)", "Schonfrist neue Konten (Tage)"]
    )
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for t in info.tenants:
        ws.append(
            [safe_text(t.name), t.inactive_days, t.long_inactive_days, t.new_account_grace_days]
        )
    ws.append([])
    ws.append(["Filter"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    for key, value in info.filters:
        ws.append([safe_text(key), safe_text(value)])
    ws.append([])
    ws.append(["Hinweis", "Das Tool liest nur; Konten in Entra und AD werden nie verändert."])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 30


def build_xlsx(rows: list[AccountRow], info: ExportInfo, tz: tzinfo) -> bytes:
    """Baut die Excel-Datei mit den Blättern „Konten“, „Lizenzen“ und „Info“."""
    wb = Workbook()
    _accounts_sheet(wb, rows, tz)
    _licenses_sheet(wb, rows)
    _info_sheet(wb, info, len(rows), tz)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
