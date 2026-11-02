"""Verwaltungsbefehle: ``python -m app.cli {tenants|check-entra|sync-entra} [kürzel]``.

Ausgaben enthalten nur Zähler, Status und Mandantennamen, keine Kontodaten.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import ConfigError, Settings
from app.connectors.check import check_entra
from app.connectors.graph import GraphError
from app.core.sync import SyncError, build_graph_client, run_entra_sync
from app.db.models import RunStatus, Tenant
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


def check_entra_cmd(settings: Settings, slug: str) -> int:
    """Testet Token, Organisation, SKUs und ``signInActivity`` eines Mandanten."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
            if tenant is None:
                print(f"Mandant '{slug}' unbekannt")
                return 2
            client = build_graph_client(tenant, settings.secrets_dir)
        result = check_entra(client)
    except (GraphError, ConfigError, SyncError) as exc:
        print(f"FEHLER: {exc}")
        return 1
    finally:
        engine.dispose()
    org = result.organization
    print(f"Token: ok\nOrganisation: {org.display_name} ({len(org.domains)} Domänen)")
    print(f"SKUs: {result.sku_count}")
    print(f"signInActivity: {'verfügbar' if result.sign_in_available else 'NICHT verfügbar'}")
    return 0


def sync_entra_cmd(settings: Settings, slug: str) -> int:
    """Führt einen Entra-Abruf aus und gibt das Ergebnis aus (Exit 1 bei Fehlschlag)."""
    engine = create_db_engine(settings.database_url)
    try:
        result = run_entra_sync(make_session_factory(engine), slug, settings.secrets_dir)
    except SyncError as exc:
        print(f"FEHLER: {exc}")
        return 2
    finally:
        engine.dispose()
    print(f"Lauf {result.run_id}: {result.status.value}")
    print(f"Konten: {result.account_count}\nSKUs: {result.sku_count}")
    if result.warnings:
        print(f"Warnungen: {', '.join(result.warnings)}")
    if result.error:
        print(f"Fehler: {result.error}")
    return 1 if result.status is RunStatus.FAILED else 0


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt der Befehlszeile."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tenants", help="Mandanten auflisten")
    for name, text in (
        ("check-entra", "Entra-Verbindung eines Mandanten testen"),
        ("sync-entra", "Entra-Abruf eines Mandanten ausführen"),
    ):
        sub.add_parser(name, help=text).add_argument("slug", help="Mandantenkürzel, z. B. cg")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "tenants":
        return list_tenants(settings)
    if args.command == "check-entra":
        return check_entra_cmd(settings, args.slug)
    return sync_entra_cmd(settings, args.slug)


if __name__ == "__main__":
    sys.exit(main())
