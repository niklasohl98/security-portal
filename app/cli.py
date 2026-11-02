"""Kleine Verwaltungsbefehle: ``python -m app.cli tenants``."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import Settings
from app.db.models import Tenant
from app.db.session import create_db_engine, make_session_factory


def list_tenants(settings: Settings) -> int:
    """Gibt die angelegten Mandanten aus (Kürzel, Name, Quellen, Schwellwert)."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            for t in session.scalars(select(Tenant).order_by(Tenant.slug)):
                sources = "+".join(
                    s for s, ok in (("entra", t.entra_tenant_id), ("ad", t.ad_domain)) if ok
                )
                state = "aktiv" if t.enabled else "deaktiviert"
                print(f"{t.slug}\t{t.name}\t{sources}\t{t.inactive_days}d\t{state}")
    finally:
        engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt der Befehlszeile."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["tenants"], help="tenants: Mandanten auflisten")
    parser.parse_args(argv)
    return list_tenants(Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
