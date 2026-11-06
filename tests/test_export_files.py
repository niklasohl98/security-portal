from __future__ import annotations

import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook
from sqlalchemy.orm import Session, sessionmaker

from app.core.accounts import AccountFilter, AccountRow, apply_filter, load_accounts
from app.db.models import Identity
from app.export.columns import COLUMNS, HEADERS, format_date, safe_text, text_row
from app.export.csv import BOM, build_csv
from app.export.xlsx import SHEET_ACCOUNTS, STATUS_FILLS, ExportInfo, TenantInfo, build_xlsx
from tests.identity_fakes import NOW, get_tenant, seed_world

BERLIN = ZoneInfo("Europe/Berlin")
INFO = ExportInfo(
    created_at=NOW,
    tenants=[TenantInfo("CG", 30, 90, 14)],
    filters=[("Mandant", "CG"), ("Suche", "-")],
)


@pytest.fixture
def rows(factory: sessionmaker[Session]) -> list[AccountRow]:
    with factory.begin() as s:
        seed_world(s)
    with factory() as s:
        return apply_filter(load_accounts(s, get_tenant(s), NOW), AccountFilter(flagged_only=False))


def _by_name(rows: list[AccountRow], name: str) -> AccountRow:
    return next(r for r in rows if r.display_name == name)


def test_safe_text_defuses_formulas_but_leaves_normal_text() -> None:
    for danger in ("=1+1", "+49 170", "-5", "@SUMME(A1)", "\tx", "\rx", "\nx"):
        assert safe_text(danger) == "'" + danger
    for fine in ("Anna", "a=b", "", "Müller; Söhne", "12"):
        assert safe_text(fine) == fine


def test_dates_are_formatted_in_the_given_timezone() -> None:
    assert format_date(datetime(2026, 11, 6, 12, 0, tzinfo=UTC), BERLIN) == "06.11.2026 13:00"
    assert (
        format_date(datetime(2026, 7, 1, 12, 0, tzinfo=UTC), BERLIN) == "01.07.2026 14:00"
    )  # Sommerzeit
    assert format_date(None, BERLIN) == ""


def test_columns_have_unique_headers_and_a_value_for_every_row(rows: list[AccountRow]) -> None:
    assert len(set(HEADERS)) == len(HEADERS) == len(COLUMNS)
    for row in rows:
        assert len(text_row(row, BERLIN)) == len(COLUMNS)


def test_text_row_content(rows: list[AccountRow]) -> None:
    values = dict(zip(HEADERS, text_row(_by_name(rows, "Person 1"), BERLIN), strict=True))
    assert values["Mandant"] == "CG"
    assert values["Konto (UPN)"] == "user1@beispiel.test"
    assert values["Status"] == "Inaktiv (lang)"
    assert values["Geflaggt"] == "Ja"
    assert values["Tage seit Anmeldung"] == "200"
    assert values["Lizenzen"] == "Microsoft 365 E3"  # kostenlose fehlen
    assert values["Empfehlung"] == "Lizenz entziehen, dann deaktivieren"
    assert values["Letzte Anmeldung"] == "20.04.2026 14:00"
    assert values["Quelle"] == "Entra"
    assert values["Ausnahme bis"] == ""
    ad = dict(zip(HEADERS, text_row(_by_name(rows, "nur.ad"), BERLIN), strict=True))
    assert ad["Anmeldename"] == "nur.ad"
    assert ad["Letzte Anmeldung AD"] == "17.09.2026 14:00"
    assert ad["Letzte Anmeldung Entra"] == ""


def test_csv_is_excel_ready_semicolon_bom_crlf(rows: list[AccountRow]) -> None:
    data = build_csv(rows, BERLIN)
    assert data.startswith(BOM.encode("utf-8"))  # EF BB BF
    text = data.decode("utf-8-sig")
    lines = text.split("\r\n")
    assert lines[0] == ";".join(HEADERS)
    assert len(lines) == len(rows) + 2  # Kopfzeile, Konten, leeres Ende
    assert lines[-1] == ""
    assert "\n" not in text.replace("\r\n", "")  # nur CRLF als Zeilenende


def test_csv_keeps_umlauts_and_quotes_special_characters() -> None:
    row = AccountRow(
        identity_id=1,
        tenant_slug="cg",
        tenant_name="CG",
        display_name='Müller; "Söhne"\nGmbH',
        upn="mü@beispiel.test",
        sam_account_name=None,
        mail=None,
        status="active",
        account_type="guest",
        account_type_manual=False,
        flagged=False,
        recommendation="Gast löschen",
        last_login_at=None,
        days_since_login=None,
        source="entra",
    )
    text = build_csv([row], BERLIN).decode("utf-8-sig")
    assert '"Müller; ""Söhne""\nGmbH"' in text
    assert "mü@beispiel.test" in text
    assert "Gast löschen" in text


def test_csv_defuses_formulas_in_every_text_cell() -> None:
    row = AccountRow(
        identity_id=1,
        tenant_slug="cg",
        tenant_name="=CG()",
        display_name='=HYPERLINK("x")',
        upn="+evil",
        sam_account_name="-1",
        mail="@x",
        status="active",
        account_type="guest",
        account_type_manual=False,
        flagged=False,
        recommendation="=cmd|' /C calc'!A0",
        last_login_at=None,
        days_since_login=None,
        source="ad",
    )
    cells = text_row(row, BERLIN)
    assert not any(c.startswith(("=", "+", "-", "@")) for c in cells if c)
    assert cells[0] == "'=CG()"


def test_csv_without_rows_has_only_the_header() -> None:
    assert build_csv([], BERLIN).decode("utf-8-sig") == ";".join(HEADERS) + "\r\n"


def _workbook(rows: list[AccountRow], info: ExportInfo = INFO) -> object:
    return load_workbook(io.BytesIO(build_xlsx(rows, info, BERLIN)))


def test_xlsx_has_the_three_sheets_and_a_filterable_frozen_table(rows: list[AccountRow]) -> None:
    wb = _workbook(rows)
    assert wb.sheetnames == ["Konten", "Lizenzen", "Info"]  # type: ignore[attr-defined]
    ws = wb[SHEET_ACCOUNTS]  # type: ignore[index]
    assert [c.value for c in ws[1]] == list(HEADERS)
    assert ws.max_row == len(rows) + 1
    assert ws.freeze_panes == "A2"
    (table,) = ws.tables.values()
    assert table.ref == f"A1:{ws.cell(row=1, column=len(HEADERS)).column_letter}{len(rows) + 1}"
    assert table.tableStyleInfo.name == "TableStyleMedium2"


def test_xlsx_status_is_colored_and_dates_are_real_excel_dates(rows: list[AccountRow]) -> None:
    ws = _workbook(rows)[SHEET_ACCOUNTS]  # type: ignore[index]
    status_col = HEADERS.index("Status") + 1
    login_col = HEADERS.index("Letzte Anmeldung") + 1
    seen = {}
    for r in range(2, ws.max_row + 1):
        status = ws.cell(row=r, column=status_col).value
        seen[status] = ws.cell(row=r, column=status_col).fill.fgColor.rgb
    assert seen["Inaktiv (lang)"].endswith(STATUS_FILLS["inactive_long"])
    assert seen["Aktiv"].endswith(STATUS_FILLS["active"])
    person1 = next(
        r
        for r in range(2, ws.max_row + 1)
        if ws.cell(row=r, column=1).value and ws.cell(row=r, column=2).value == "Person 1"
    )
    cell = ws.cell(row=person1, column=login_col)
    assert cell.value == datetime(2026, 4, 20, 14, 0)  # noqa: DTZ001 - Excel: Ortszeit ohne Zeitzone
    assert cell.number_format == "DD.MM.YYYY HH:MM"
    days = ws.cell(row=person1, column=HEADERS.index("Tage seit Anmeldung") + 1)
    assert days.value == 200
    assert isinstance(days.value, int)


def test_xlsx_never_stores_formulas_from_data(rows: list[AccountRow]) -> None:
    evil = AccountRow(
        identity_id=9,
        tenant_slug="cg",
        tenant_name="CG",
        display_name="=1+1",
        upn="@x",
        sam_account_name="+SUM(1)",
        mail=None,
        status="never",
        account_type="guest",
        account_type_manual=False,
        flagged=True,
        recommendation="-2",
        last_login_at=None,
        days_since_login=None,
        source="entra",
    )
    ws = _workbook([evil])[SHEET_ACCOUNTS]  # type: ignore[index]
    for cell in ws[2]:
        assert cell.data_type != "f"
    assert ws.cell(row=2, column=2).value == "'=1+1"


def test_xlsx_license_sheet_counts_flagged_paid_licenses(rows: list[AccountRow]) -> None:
    ws = _workbook(rows)["Lizenzen"]  # type: ignore[index]
    got = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert sorted(got) == sorted([["CG", "Microsoft 365 E3", 1], ["CG", "NEU_X", 1]])
    assert [c.value for c in ws[1]] == ["Mandant", "Lizenz", "Konten (geflaggt)"]


def test_xlsx_info_sheet_lists_date_thresholds_and_filters(rows: list[AccountRow]) -> None:
    ws = _workbook(rows)["Info"]  # type: ignore[index]
    cells = {c.value for r in ws.iter_rows() for c in r if c.value is not None}
    assert datetime(2026, 11, 6, 13, 0) in cells  # noqa: DTZ001 - Excel: Ortszeit ohne Zeitzone
    assert "Europe/Berlin" in cells
    assert len(rows) in cells
    assert {30, 90, 14} <= cells
    assert {"Mandant", "Suche"} <= cells
    assert any("nie verändert" in str(v) for v in cells)


def test_xlsx_without_rows_is_still_a_valid_workbook() -> None:
    wb = _workbook([])
    ws = wb[SHEET_ACCOUNTS]  # type: ignore[index]
    assert [c.value for c in ws[1]] == list(HEADERS)
    assert wb["Lizenzen"].max_row == 1  # type: ignore[index]


def test_two_tenants_in_one_license_sheet(factory: sessionmaker[Session]) -> None:
    with factory.begin() as s:
        seed_world(s)
        s.add(Identity(tenant_id=get_tenant(s, "cvh").id, key="x", display_name="Fremd"))
    with factory() as s:
        all_rows = load_accounts(s, get_tenant(s), NOW) + load_accounts(
            s, get_tenant(s, "cvh"), NOW
        )
    ws = _workbook(all_rows)["Lizenzen"]  # type: ignore[index]
    assert {r[0].value for r in ws.iter_rows(min_row=2)} == {"CG"}  # CVH hat nichts Geflaggtes
