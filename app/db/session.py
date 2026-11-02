"""Datenbank-Engine, Sitzungen und Migrationen."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(url: str) -> Engine:
    """Erzeugt die Engine. Bei SQLite: Fremdschlüssel an, WAL-Modus für parallele Leser."""
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: sqlite3.Connection, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sitzungsfabrik; ``expire_on_commit=False``, damit Objekte nach dem Commit lesbar bleiben."""
    return sessionmaker(engine, expire_on_commit=False)


def run_migrations(database_url: str) -> None:
    """Führt ``alembic upgrade head`` aus (beim Start der Anwendung)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
