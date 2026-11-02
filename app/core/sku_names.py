"""Gepflegte Zuordnung SKU-Teilenummer -> Anzeigename (aus ``sku_names.yaml`` in die Tabelle)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import ConfigError
from app.db.models import SkuName

log = logging.getLogger(__name__)


class _SkuEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    part_number: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    free: bool = False


class _SkuFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skus: list[_SkuEntry]


def load_sku_names(path: Path) -> list[_SkuEntry]:
    """Liest die SKU-Tabelle. Fehlt die Datei, ist die Liste leer (Namen fallen auf die
    Teilenummer zurück).

    Raises:
        ConfigError: Datei vorhanden, aber ungültig (Meldung ohne Eingabewerte).
    """
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return _SkuFile.model_validate(raw).skus
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"SKU-Datei '{path}' nicht lesbar oder kein gültiges YAML") from exc
    except ValueError as exc:
        errors = getattr(exc, "errors", None)
        details = (
            "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in errors(include_input=False)
            )
            if errors
            else "ungültig"
        )
        raise ConfigError(f"SKU-Datei '{path}' ungültig: {details}") from exc


def sync_sku_names(session: Session, entries: list[_SkuEntry]) -> None:
    """Gleicht die Tabelle ``sku_name`` mit der Datei ab (anlegen und aktualisieren).

    Einträge, die nur in der Datenbank stehen, bleiben erhalten.
    """
    for e in entries:
        row = session.get(SkuName, e.part_number)
        if row is None:
            session.add(SkuName(part_number=e.part_number, display_name=e.name, is_free=e.free))
        else:
            row.display_name = e.name
            row.is_free = e.free
