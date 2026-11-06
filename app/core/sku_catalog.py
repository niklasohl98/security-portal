"""Katalog der Microsoft-SKU-Namen: aus Microsofts Referenztabelle erzeugen und mitliefern.

Graph liefert zu einer Lizenz nur die Teilenummer (``skuPartNumber``, z. B. ``SPB``). Die lesbaren
Namen stehen in Microsofts Tabelle „Product names and service plan identifiers for licensing“ (CSV,
``CATALOG_URL``). ``parse_microsoft_csv`` macht daraus die Zuordnung Teilenummer -> Name; der Befehl
``python -m app.cli build-sku-catalog`` schreibt sie nach ``app/data/sku_catalog.yaml``, das mit dem
Image ausgeliefert und beim Start in ``sku_name`` geladen wird. Eine eigene ``sku_names.yaml`` hat
Vorrang (siehe ``sku_names.py``).

Regeln: Teilenummern und Namen werden von Leerraum befreit; steht zu einer Teilenummer mehr als ein
Name (Umbenennungen), gilt der erste in der Datei (der neuere). Einträge über den Grenzen von
``sku_name`` (Nummer über 100, Name über 200 Zeichen; es sind zwei US-Behörden-Pakete) werden
übersprungen und gezählt. „Kostenlos“ sind nur eindeutige Fälle (siehe ``is_free``): Eine falsch
als kostenlos eingestufte Lizenz würde in den freiwerdenden Lizenzen fehlen, eine falsch als
bezahlt eingestufte nur zusätzlich erscheinen.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

import yaml

CATALOG_URL = (
    "https://download.microsoft.com/download/e/3/e/e3e9faf2-f28b-490a-9ada-c6089a1fc5b0/"
    "Product%20names%20and%20service%20plan%20identifiers%20for%20licensing.csv"
)
MAX_PART_NUMBER = 100  # Spaltenbreiten von ``sku_name``, siehe ``db/models.py``
MAX_NAME = 200
_FREE_NAME = re.compile(r"\bfree\b", re.IGNORECASE)
_FREE_SUFFIXES = ("_FREE", "_VIRAL")  # Selbstbedienungs-Testversionen und kostenlose Pläne
_FREE_EXTRA = frozenset({"POWER_BI_STANDARD"})  # „Microsoft Fabric (Free)“ trägt die alte Nummer


@dataclass(frozen=True)
class CatalogEntry:
    """Eine Zeile des Katalogs."""

    part_number: str
    name: str
    free: bool


@dataclass(frozen=True)
class ParsedCatalog:
    """Ergebnis von ``parse_microsoft_csv``: die Einträge und wie viele übersprungen wurden."""

    entries: list[CatalogEntry]
    skipped: int  # zu lange Nummern oder Namen


def is_free(part_number: str, name: str) -> bool:
    """Ob die SKU kostenlos ist: Name enthält „Free“ oder Nummer endet auf ``_FREE``/``_VIRAL``."""
    return bool(
        _FREE_NAME.search(name)
        or part_number.endswith(_FREE_SUFFIXES)
        or part_number in _FREE_EXTRA
    )


def parse_microsoft_csv(text: str) -> ParsedCatalog:
    """Liest Microsofts CSV (Spalten ``Product_Display_Name``, ``String_Id``), sortiert nach Nummer.

    Raises:
        ValueError: wenn die Spalten fehlen (dann ist es nicht die erwartete Tabelle).
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not {"Product_Display_Name", "String_Id"} <= set(reader.fieldnames or []):
        raise ValueError("Spalten Product_Display_Name und String_Id fehlen")
    names: dict[str, str] = {}
    for row in reader:
        part = (row["String_Id"] or "").strip()
        name = (row["Product_Display_Name"] or "").strip()
        if part and name:
            names.setdefault(part, name)  # bei Umbenennungen gewinnt der erste Name der Datei
    fitting = [p for p in sorted(names) if len(p) <= MAX_PART_NUMBER and len(names[p]) <= MAX_NAME]
    return ParsedCatalog(
        [CatalogEntry(p, names[p], is_free(p, names[p])) for p in fitting],
        skipped=len(names) - len(fitting),
    )


def render_catalog_yaml(entries: list[CatalogEntry], retrieved_on: str) -> str:
    """Baut die YAML-Datei im Format von ``sku_names.yaml`` mit Quellenangabe im Kopf."""
    header = (
        "# Katalog der Microsoft-SKU-Namen (Teilenummer -> Anzeigename), erzeugt mit\n"
        "#   python -m app.cli build-sku-catalog <csv> app/data/sku_catalog.yaml\n"
        f'# Quelle: Microsoft, "Product names and service plan identifiers for licensing",\n'
        f"# abgerufen am {retrieved_on}: {CATALOG_URL}\n"
        "# Nicht von Hand ändern: eigene Namen und Korrekturen gehören in config/sku_names.yaml\n"
        "# (hat Vorrang). free: true = kostenlos oder Testversion, zählt nicht als Lizenz.\n"
    )
    body = yaml.safe_dump(
        {
            "skus": [
                {"part_number": e.part_number, "name": e.name, **({"free": True} if e.free else {})}
                for e in entries
            ]
        },
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=None,
        width=1000,
    )
    return header + body
