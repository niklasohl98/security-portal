"""Verwaltungsbefehle von ``python -m app.cli``.

Aufruf: ``{tenants|check-entra|sync-entra|check-ad|sync-ad|refresh-identities} [kürzel]``.

Ausgaben enthalten nur Zähler, Status und Mandantennamen, keine Kontodaten.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import ConfigError, Settings
from app.connectors.ad import DcReport, NoHealthyDcError, collect_ad, discover_dcs
from app.connectors.ad_client import AdError
from app.connectors.check import check_entra
from app.connectors.graph import GraphError
from app.core.ad_sync import build_ad_runtime, run_ad_sync
from app.core.identities import run_identity_refresh
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


def _print_dcs(dcs: list[DcReport]) -> None:
    """Gibt die Prüfergebnisse je DC aus (nur technische Angaben)."""
    for d in dcs:
        count = "-" if d.user_count is None else str(d.user_count)
        print(f"  {d.host}\t{d.status.value}\t{d.reason or '-'}\t{count} Konten")


def check_ad_cmd(settings: Settings, slug: str) -> int:
    """Prüft DCs und liest die Konten, ohne zu speichern (Trockenlauf)."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
            if tenant is None:
                print(f"Mandant '{slug}' unbekannt")
                return 2
            rt = build_ad_runtime(tenant, settings.secrets_dir, settings.tenants_file)
        hosts = discover_dcs(rt.domain, rt.configured_dcs)
        print(f"DCs gefunden: {len(hosts)}")
        result = collect_ad(hosts, rt.opener, rt.service_account)
    except (AdError, ConfigError, SyncError) as exc:
        print(f"FEHLER: {exc}")
        if isinstance(exc, NoHealthyDcError):
            _print_dcs(exc.reports)  # zeigt je DC, woran es liegt
        return 1
    finally:
        engine.dispose()
    _print_dcs(result.dcs)
    print(f"Konten: {len(result.accounts)} (Stammdaten von {result.master_host})")
    notices = ", ".join(result.warnings + rt.notices) or "keine"
    print(f"Warnungen: {notices}")
    return 0


def sync_ad_cmd(settings: Settings, slug: str) -> int:
    """Führt einen AD-Abruf aus und gibt das Ergebnis aus (Exit 1 bei Fehlschlag)."""
    engine = create_db_engine(settings.database_url)
    try:
        result = run_ad_sync(
            make_session_factory(engine), slug, settings.secrets_dir, settings.tenants_file
        )
    except SyncError as exc:
        print(f"FEHLER: {exc}")
        return 2
    finally:
        engine.dispose()
    print(f"Lauf {result.run_id}: {result.status.value}")
    print(f"Konten: {result.account_count}")
    _print_dcs(result.dcs)
    if result.warnings:
        print(f"Warnungen: {', '.join(result.warnings)}")
    if result.error:
        print(f"Fehler: {result.error}")
    return 1 if result.status is RunStatus.FAILED else 0


def refresh_identities_cmd(settings: Settings, slug: str) -> int:
    """Führt Entra und AD zusammen und berechnet Status, Kontoart und Empfehlung neu."""
    engine = create_db_engine(settings.database_url)
    try:
        result = run_identity_refresh(make_session_factory(engine), slug)
    except SyncError as exc:
        print(f"FEHLER: {exc}")
        return 2
    finally:
        engine.dispose()
    if not result.evaluated:
        print("Keine erfolgreichen Läufe vorhanden; nichts berechnet.")
        return 1
    print(f"Identitäten: {result.identities}\nGeflaggt: {result.flagged}")
    if result.matches:
        print(
            "Zuordnung Entra/AD: "
            + ", ".join(f"{k}={v}" for k, v in sorted(result.matches.items()))
        )
    if result.missing:
        print(f"Zur Prüfung (verschwunden): {result.missing}, siehe /review")
    if result.ambiguous:
        print(f"Mehrdeutige Zuordnungen: {result.ambiguous}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt der Befehlszeile."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tenants", help="Mandanten auflisten")
    for name, text in (
        ("check-entra", "Entra-Verbindung eines Mandanten testen"),
        ("sync-entra", "Entra-Abruf eines Mandanten ausführen"),
        ("check-ad", "AD-Verbindung und DCs eines Mandanten testen (speichert nichts)"),
        ("sync-ad", "AD-Abruf eines Mandanten ausführen"),
        ("refresh-identities", "Konten zusammenführen, Status und Empfehlung berechnen"),
    ):
        sub.add_parser(name, help=text).add_argument("slug", help="Mandantenkürzel, z. B. cg")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "tenants":
        return list_tenants(settings)
    if args.command == "check-entra":
        return check_entra_cmd(settings, args.slug)
    if args.command == "check-ad":
        return check_ad_cmd(settings, args.slug)
    if args.command == "sync-ad":
        return sync_ad_cmd(settings, args.slug)
    if args.command == "refresh-identities":
        return refresh_identities_cmd(settings, args.slug)
    return sync_entra_cmd(settings, args.slug)


if __name__ == "__main__":
    sys.exit(main())
