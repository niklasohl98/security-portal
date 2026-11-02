"""Alembic-Umgebung. Die Datenbank-URL kommt aus der Alembic-Konfiguration (siehe session.py)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Migriert gegen die konfigurierte Datenbank.

    ``engine.begin()`` committet am Ende, sonst geht der Eintrag in ``alembic_version`` verloren
    und die nächste Anwendung versucht dieselben Migrationen erneut. ``render_as_batch`` wegen
    SQLite. Kein ``PRAGMA foreign_keys`` hier: innerhalb einer Transaktion wirkungslos, und der
    Batch-Modus baut Tabellen um.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.begin() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        context.run_migrations()
    connectable.dispose()


run_migrations_online()
