"""Alembic-Umgebung. Die Datenbank-URL kommt aus der Alembic-Konfiguration (siehe session.py)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Migriert gegen die konfigurierte Datenbank; ``render_as_batch`` wegen SQLite."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
