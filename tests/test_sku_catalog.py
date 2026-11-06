from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.config import BUNDLED_SKU_CATALOG, Settings
from app.core.sku_catalog import (
    CATALOG_URL,
    CatalogEntry,
    is_free,
    parse_microsoft_csv,
    render_catalog_yaml,
)
from app.core.sku_names import load_sku_names
from app.db.models import SkuName
from app.main import create_app
from tests.web_fakes import identity_id


def _session(client: TestClient) -> Session:
    session: Session = cast(FastAPI, client.app).state.session_factory()
    return session


HEADER = "Product_Display_Name,String_Id,GUID,Service_Plan_Name,Service_Plan_Id,Friendly\n"


def _csv(*rows: tuple[str, str]) -> str:
    return HEADER + "".join(f'"{name}",{part},guid,plan,planid,x\n' for name, part in rows)


@pytest.mark.parametrize(
    ("part", "name", "free"),
    [
        ("FLOW_FREE", "Microsoft Power Automate Free", True),
        ("POWER_BI_STANDARD", "Microsoft Fabric (Free)", True),
        ("TEAMS_FREE", "Microsoft Teams (Free)", True),
        ("POWERAPPS_VIRAL", "Microsoft Power Apps Plan 2 Trial", True),
        ("SMB_APPS", "Business Apps (free)", True),
        ("SPB", "Microsoft 365 Business Premium", False),
        ("POWER_BI_PRO", "Power BI Pro", False),
        ("DYNAMICS_FREEDOM", "Dynamics Freedom Pack", False),  # „free“ nur als ganzes Wort
        ("O365_BUSINESS_PREMIUM", "Microsoft 365 Business Standard", False),
    ],
)
def test_free_rule(part: str, name: str, free: bool) -> None:
    assert is_free(part, name) is free


def test_parse_reads_the_microsoft_columns_and_sorts_by_part_number() -> None:
    parsed = parse_microsoft_csv(
        _csv(("Power BI Pro", "POWER_BI_PRO"), ("Microsoft 365 Business Premium", "SPB"))
    )
    assert parsed.skipped == 0
    assert parsed.entries == [
        CatalogEntry("POWER_BI_PRO", "Power BI Pro", False),
        CatalogEntry("SPB", "Microsoft 365 Business Premium", False),
    ]


def test_parse_handles_bom_whitespace_and_repeated_rows() -> None:
    text = "﻿" + _csv(
        ("  Power BI Pro  ", " POWER_BI_PRO\t"),
        ("Power BI Pro", "POWER_BI_PRO"),  # jede SKU steht mit vielen Dienstplänen mehrfach
        ("Ohne Nummer", ""),
        ("", "OHNE_NAME"),
    )
    assert parse_microsoft_csv(text).entries == [
        CatalogEntry("POWER_BI_PRO", "Power BI Pro", False)
    ]


def test_a_renamed_sku_keeps_the_first_name_of_the_file() -> None:
    text = _csv(
        ("Microsoft Copilot Studio User License", "VIRTUAL_AGENT_USL"),
        ("Power Virtual Agent User License", "VIRTUAL_AGENT_USL"),
    )
    (entry,) = parse_microsoft_csv(text).entries
    assert entry.name == "Microsoft Copilot Studio User License"


def test_entries_that_do_not_fit_the_database_columns_are_skipped_and_counted() -> None:
    text = _csv(
        ("Passt", "OK_SKU"),
        ("Zu lange Nummer", "X" * 101),
        ("Y" * 201, "ZU_LANGER_NAME"),
        ("Genau an der Grenze", "Z" * 100),
    )
    parsed = parse_microsoft_csv(text)
    assert [e.part_number for e in parsed.entries] == ["OK_SKU", "Z" * 100]
    assert parsed.skipped == 2


@pytest.mark.parametrize("text", ["", "a,b\n1,2\n", "Product_Display_Name,Other\nx,y\n"])
def test_parse_rejects_other_tables(text: str) -> None:
    with pytest.raises(ValueError, match="Spalten"):
        parse_microsoft_csv(text)


def test_rendered_yaml_roundtrips_through_the_normal_loader(tmp_path: Path) -> None:
    entries = [
        CatalogEntry(
            "1234", "Nur Ziffern", False
        ),  # müsste als Zahl gelesen werden, wenn ungequotet
        CatalogEntry("FLOW_FREE", "Microsoft Power Automate Free", True),
        CatalogEntry("X", 'Name: mit "Zeichen", Komma # und Umlaute äöü', False),
    ]
    path = tmp_path / "katalog.yaml"
    path.write_text(render_catalog_yaml(entries, "2026-11-06"), encoding="utf-8")
    loaded = load_sku_names(path)
    assert [(e.part_number, e.name, e.free) for e in loaded] == [
        (e.part_number, e.name, e.free) for e in entries
    ]


def test_rendered_yaml_names_the_source() -> None:
    text = render_catalog_yaml([], "2026-11-06")
    assert CATALOG_URL in text
    assert "abgerufen am 2026-11-06" in text
    assert "config/sku_names.yaml" in text  # Hinweis, wohin eigene Namen gehören


def test_cli_builds_the_catalog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src, out = tmp_path / "in.csv", tmp_path / "out.yaml"
    src.write_text(
        _csv(("Microsoft Power Automate Free", "FLOW_FREE"), ("Power BI Pro", "POWER_BI_PRO")),
        encoding="utf-8",
    )
    assert cli_main(["build-sku-catalog", str(src), str(out)]) == 0
    printed = capsys.readouterr().out
    assert "SKUs: 2" in printed
    assert "Davon kostenlos: 1" in printed
    assert "Übersprungen (Nummer oder Name zu lang): 0" in printed
    assert [e.part_number for e in load_sku_names(out)] == ["FLOW_FREE", "POWER_BI_PRO"]


def test_cli_reports_unreadable_or_wrong_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.yaml"
    assert cli_main(["build-sku-catalog", str(tmp_path / "gibtsnicht.csv"), str(out)]) == 2
    wrong = tmp_path / "falsch.csv"
    wrong.write_text("a,b\n1,2\n", encoding="utf-8")
    assert cli_main(["build-sku-catalog", str(wrong), str(out)]) == 2
    assert "FEHLER" in capsys.readouterr().out
    assert not out.exists()


# --- der mitgelieferte Katalog ---


def test_the_bundled_catalog_is_valid_and_complete_enough() -> None:
    entries = {e.part_number: e for e in load_sku_names(BUNDLED_SKU_CATALOG)}
    assert len(entries) >= 600
    assert entries["SPB"].name == "Microsoft 365 Business Premium"
    assert entries["O365_BUSINESS_PREMIUM"].name == "Microsoft 365 Business Standard"
    assert entries["MDATP_XPLAT"].name == "Microsoft Defender for Endpoint P2_XPLAT"
    assert entries["POWER_BI_PRO"].name == "Power BI Pro"
    assert entries["PBI_PREMIUM_PER_USER"].name == "Power BI Premium Per User"
    assert {k for k, e in entries.items() if e.free} >= {
        "FLOW_FREE",
        "POWER_BI_STANDARD",
        "TEAMS_FREE",
    }
    assert not entries["SPB"].free
    assert not entries["POWER_BI_PRO"].free


def test_the_bundled_catalog_is_exactly_what_the_generator_would_write() -> None:
    """Eine von Hand geänderte Katalogdatei fällt auf: Sie muss neu erzeugt werden."""
    raw = yaml.safe_load(BUNDLED_SKU_CATALOG.read_text(encoding="utf-8"))
    parts = [e["part_number"] for e in raw["skus"]]
    assert parts == sorted(parts)
    assert len(parts) == len(set(parts))
    assert all(e["name"] == e["name"].strip() for e in raw["skus"])


# --- Start der Anwendung ---


def test_the_app_loads_the_catalog_and_a_user_file_overrides_it(
    settings: Settings, tmp_path: Path
) -> None:
    mine = tmp_path / "sku_names.yaml"
    mine.write_text(
        "skus:\n"
        "  - {part_number: SPB, name: Eigener Name}\n"
        "  - {part_number: POWER_BI_PRO, name: Power BI Pro, free: true}\n",
        encoding="utf-8",
    )
    cfg = replace(settings, sku_catalog_file=BUNDLED_SKU_CATALOG, sku_names_file=mine)
    with TestClient(create_app(cfg)) as client, _session(client) as s:
        rows = {r.part_number: r for r in s.scalars(select(SkuName))}
    assert len(rows) >= 600
    assert rows["O365_BUSINESS_PREMIUM"].display_name == "Microsoft 365 Business Standard"
    assert rows["FLOW_FREE"].is_free is True
    assert rows["SPB"].display_name == "Eigener Name"  # die eigene Datei gewinnt
    assert rows["POWER_BI_PRO"].is_free is True  # auch beim kostenlos-Kennzeichen


def test_the_app_starts_without_any_catalog(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client, _session(client) as s:
        assert s.scalars(select(SkuName)).all() == []


def test_a_restart_updates_names_and_keeps_user_rows(settings: Settings) -> None:
    cfg = replace(settings, sku_catalog_file=BUNDLED_SKU_CATALOG)
    with TestClient(create_app(cfg)) as client, _session(client) as s, s.begin():
        s.get_one(SkuName, "SPB").display_name = "Veraltet"
        s.add(SkuName(part_number="NUR_IN_DB", display_name="Bleibt", is_free=False))
    with TestClient(create_app(cfg)) as client, _session(client) as s:
        assert s.get_one(SkuName, "SPB").display_name == "Microsoft 365 Business Premium"
        assert s.get_one(SkuName, "NUR_IN_DB").display_name == "Bleibt"


# --- Anzeige ---


def test_detail_shows_the_name_with_the_small_part_number(site: TestClient) -> None:
    from tests.identity_fakes import seed_world
    from tests.web_fakes import open_session

    with open_session(site) as s, s.begin():
        seed_world(s)
    text = site.get(f"/accounts/cg/{identity_id(site, 'Person 1')}").text
    assert "Microsoft 365 E3" in text
    assert '<span class="small muted">SPE_E3</span>' in text
    unknown = site.get(f"/accounts/cg/{identity_id(site, 'Person 2')}").text
    assert "NEU_X" in unknown
    assert '<span class="small muted">NEU_X</span>' not in unknown  # Name = Nummer: nicht doppelt
