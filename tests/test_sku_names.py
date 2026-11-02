from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import ConfigError
from app.core.sku_names import load_sku_names, sync_sku_names
from app.db.models import SkuName
from app.db.session import create_db_engine, make_session_factory, run_migrations

EXAMPLE = Path(__file__).parent.parent / "config" / "sku_names.example.yaml"


def test_example_file_is_valid_and_marks_free_skus() -> None:
    entries = load_sku_names(EXAMPLE)
    by_pn = {e.part_number: e for e in entries}
    assert by_pn["SPE_E3"].name == "Microsoft 365 E3"
    assert by_pn["FLOW_FREE"].free is True
    assert by_pn["SPE_E3"].free is False


def test_missing_file_gives_empty_list(tmp_path: Path) -> None:
    assert load_sku_names(tmp_path / "nein.yaml") == []


@pytest.mark.parametrize(
    "content",
    [
        "skus: [",
        "skus:\n  - part_number: X\n",
        "skus:\n  - {part_number: X, name: Y, extra: 1}\n",
        "[]",
    ],
)
def test_invalid_file_raises_config_error(tmp_path: Path, content: str) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_sku_names(p)


def test_sync_creates_and_updates_but_keeps_db_only_rows(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'x.db'}"
    run_migrations(url)
    engine = create_db_engine(url)
    factory = make_session_factory(engine)
    with factory.begin() as s:
        s.add(SkuName(part_number="NUR_DB", display_name="Nur in DB", is_free=False))
        sync_sku_names(s, load_sku_names(EXAMPLE))
    p = tmp_path / "neu.yaml"
    p.write_text(
        "skus:\n  - {part_number: SPE_E3, name: Neuer Name, free: true}\n", encoding="utf-8"
    )
    with factory.begin() as s:
        sync_sku_names(s, load_sku_names(p))
    with factory() as s:
        rows = {r.part_number: r for r in s.scalars(select(SkuName))}
    engine.dispose()
    assert rows["SPE_E3"].display_name == "Neuer Name"
    assert rows["SPE_E3"].is_free is True
    assert "NUR_DB" in rows
    assert "FLOW_FREE" in rows
