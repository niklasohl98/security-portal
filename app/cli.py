"""Verwaltungsbefehle von ``python -m app.cli``.

Aufruf: ``{check-entra|sync-entra|check-ad|sync-ad|refresh-identities|cycle} <kürzel>``,
``{user-add|user-reset} <name>``, ``build-sku-catalog <csv> <ausgabe>``,
``{report-preview|report-send} <kürzel>``, ``{backup-verify|restore-backup} <datei>`` oder
``{tenants|purge|user-list|backup|backup-list|mail-check}``.

Ausgaben enthalten nur Zähler, Status und Mandantennamen, keine Kontodaten.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.auth import service as auth_service
from app.auth.passwords import MIN_LENGTH
from app.cli_external_backup import add_commands, external_backup_cmd
from app.cli_mail import mail_check_cmd, report_preview_cmd, report_send_cmd
from app.config import ConfigError, Settings
from app.connectors.ad import DcReport, NoHealthyDcError, collect_ad, discover_dcs
from app.connectors.ad_client import AdError
from app.connectors.check import check_entra
from app.connectors.graph import GraphError
from app.core.ad_sync import build_ad_runtime, run_ad_sync
from app.core.backup import (
    BackupError,
    list_backups,
    restore_backup,
    run_backup,
    verify_backup,
)
from app.core.cycle import run_tenant_cycle
from app.core.identities import run_identity_refresh
from app.core.retention import purge_raw_data
from app.core.sku_catalog import parse_microsoft_csv, render_catalog_yaml
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


def cycle_cmd(settings: Settings, slug: str) -> int:
    """Führt den Zyklus eines Mandanten aus: Entra, AD (soweit konfiguriert), dann Auswertung."""
    engine = create_db_engine(settings.database_url)
    try:
        result = run_tenant_cycle(make_session_factory(engine), settings, slug)
    finally:
        engine.dispose()
    if result.skipped:
        print("Übersprungen: Mandant unbekannt, deaktiviert oder Zyklus läuft bereits.")
        return 2
    for outcome in result.outcomes:
        print(f"{outcome.source.value}: {outcome.status}")
    if result.refresh is None or not result.refresh.evaluated:
        print("Keine Auswertung (keine brauchbaren Läufe).")
        return 1
    print(f"Identitäten: {result.refresh.identities}\nGeflaggt: {result.refresh.flagged}")
    failed = any(o.status in ("failed", "error") for o in result.outcomes)
    return 1 if failed else 0


def purge_cmd(settings: Settings) -> int:
    """Löscht Rohdaten alter Läufe (Aufbewahrung ``KW_RETENTION_DAYS``)."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine).begin() as session:
            result = purge_raw_data(session, settings.retention_days)
    finally:
        engine.dispose()
    print(
        f"Läufe: {result.runs}\nEntra-Konten: {result.entra_accounts}\n"
        f"AD-Konten: {result.ad_accounts}\nLizenzbestände: {result.skus}"
    )
    return 0


_USER_ERRORS = {
    "bad_name": "Benutzername: 3 bis 64 Zeichen (Buchstaben, Ziffern, Punkt, Strich, Unterstrich)",
    "exists": "Benutzer existiert schon",
    "not_found": "Benutzer unbekannt",
    "external": "Passwort wird in Entra verwaltet",
}


def _ask_password() -> str | None:
    """Fragt ein Passwort zweimal ab (ohne Anzeige); ``None`` bei Abweichung."""
    first = getpass.getpass(f"Passwort (mindestens {MIN_LENGTH} Zeichen): ")
    if first != getpass.getpass("Passwort wiederholen: "):
        print("FEHLER: Die Eingaben stimmen nicht überein.")
        return None
    return first


def user_list_cmd(settings: Settings) -> int:
    """Listet die Benutzer (ohne Passwörter und Hashes)."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            for u in auth_service.list_users(session):
                state = "aktiv" if u.is_active else "deaktiviert"
                change = ", Passwortwechsel offen" if u.must_change_password else ""
                role = "Admin" if u.is_admin else "Benutzer"
                print(f"{u.username}\t{role}\t{state}{change}")
    finally:
        engine.dispose()
    return 0


def user_write_cmd(settings: Settings, name: str, *, reset: bool, admin: bool = False) -> int:
    """Legt einen Benutzer an oder setzt ein Passwort zurück; der Wechsel beim Login ist Pflicht."""
    password = _ask_password()
    if password is None:
        return 2
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine).begin() as session:
            if reset:
                auth_service.reset_password(session, name, password, "cli")
            else:
                auth_service.create_user(session, name, password, "cli", is_admin=admin)
    except auth_service.UserError as exc:
        print(f"FEHLER: {_USER_ERRORS.get(exc.code, exc.code)}")
        return 2
    except auth_service.PasswordChangeError as exc:
        print(f"FEHLER: Passwort nicht erlaubt ({', '.join(exc.codes)})")
        return 2
    finally:
        engine.dispose()
    print("Gespeichert. Das Passwort muss bei der ersten Anmeldung geändert werden.")
    return 0


def build_sku_catalog_cmd(csv_path: str, out_path: str) -> int:
    """Erzeugt den SKU-Katalog (YAML) aus Microsofts CSV; Quelle und Abrufdatum stehen im Kopf."""
    try:
        parsed = parse_microsoft_csv(Path(csv_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"FEHLER: {type(exc).__name__}")
        return 2
    day = datetime.now(UTC).date().isoformat()
    Path(out_path).write_text(render_catalog_yaml(parsed.entries, day), encoding="utf-8")
    free = sum(e.free for e in parsed.entries)
    print(f"SKUs: {len(parsed.entries)}\nDavon kostenlos: {free}")
    print(f"Übersprungen (Nummer oder Name zu lang): {parsed.skipped}\nGeschrieben: {out_path}")
    return 0


def _size(num_bytes: int) -> str:
    return (
        f"{num_bytes / 1024 / 1024:.1f} MiB"
        if num_bytes >= 1024 * 1024
        else f"{num_bytes / 1024:.0f} KiB"
    )


def backup_cmd(settings: Settings) -> int:
    """Legt sofort eine geprüfte Sicherung an (wie der nächtliche Lauf) und räumt alte auf."""
    engine = create_db_engine(settings.database_url)
    try:
        made = run_backup(make_session_factory(engine), settings, actor="cli")
    finally:
        engine.dispose()
    if made is None:
        print("FEHLER: Sicherung fehlgeschlagen (Details im Log, Audit-Eintrag backup.failed).")
        return 1
    print(f"Gesichert: {made.name} ({_size(made.size)}) in {settings.backup_dir}")
    return 0


def backup_list_cmd(settings: Settings) -> int:
    """Listet die vorhandenen Sicherungen, neueste zuerst."""
    files = list_backups(settings.backup_dir)
    for f in files:
        kind = "vor Migration" if f.pre_migration else "regulär"
        print(f"{f.name}\t{_size(f.size)}\t{f.created_at:%d.%m.%Y %H:%M} UTC\t{kind}")
    if not files:
        print(f"Keine Sicherungen in {settings.backup_dir}")
    return 0


def backup_verify_cmd(path: str) -> int:
    """Prüft eine Sicherungsdatei (Integrität und erwartete Tabellen)."""
    try:
        verify_backup(Path(path))
    except BackupError as exc:
        print(f"FEHLER: Sicherung nicht in Ordnung ({exc.code})")
        return 1
    print("In Ordnung.")
    return 0


def restore_backup_cmd(settings: Settings, path: str, confirmed: bool) -> int:
    """Stellt eine Sicherung als Datenbank wieder her (Anwendung vorher stoppen)."""
    if not confirmed:
        print(
            "Abbruch: Stelle sicher, dass die Anwendung gestoppt ist (docker compose stop app), "
            "und wiederhole den Befehl mit --yes."
        )
        return 2
    try:
        aside = restore_backup(Path(path), settings.database_url)
    except BackupError as exc:
        print(f"FEHLER: Wiederherstellung nicht möglich ({exc.code}); Datenbank unverändert.")
        return 1
    print(f"Wiederhergestellt. Die bisherige Datenbank liegt als {aside}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Einstiegspunkt der Befehlszeile."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    add_commands(sub)
    sub.add_parser("tenants", help="Mandanten auflisten")
    for name, text in (
        ("check-entra", "Entra-Verbindung eines Mandanten testen"),
        ("sync-entra", "Entra-Abruf eines Mandanten ausführen"),
        ("check-ad", "AD-Verbindung und DCs eines Mandanten testen (speichert nichts)"),
        ("sync-ad", "AD-Abruf eines Mandanten ausführen"),
        ("refresh-identities", "Konten zusammenführen, Status und Empfehlung berechnen"),
        ("cycle", "Entra und AD abrufen und auswerten (wie der tägliche Lauf)"),
    ):
        sub.add_parser(name, help=text).add_argument("slug", help="Mandantenkürzel, z. B. cg")
    sub.add_parser("purge", help="Rohdaten alter Läufe löschen")
    sub.add_parser("user-list", help="Benutzer des Tools auflisten")
    sub.add_parser("backup", help="Datenbank jetzt sichern (geprüft, mit Aufbewahrung)")
    sub.add_parser("backup-list", help="Vorhandene Sicherungen auflisten")
    sub.add_parser("backup-verify", help="Sicherungsdatei prüfen").add_argument("file")
    sub.add_parser("mail-check", help="Mailserver prüfen (Verbindung, Zertifikat, Anmeldung)")
    for name, text in (
        ("report-preview", "Wochenbericht eines Mandanten anzeigen (sendet nichts)"),
        ("report-send", "Wochenbericht eines Mandanten jetzt senden"),
    ):
        sub.add_parser(name, help=text).add_argument("slug", help="Mandantenkürzel, z. B. cg")
    restore = sub.add_parser(
        "restore-backup", help="Sicherung wiederherstellen (App vorher stoppen)"
    )
    restore.add_argument("file", help="Sicherungsdatei, z. B. /backup/kontenwaechter-….db")
    restore.add_argument("--yes", action="store_true", help="bestätigt, dass die App gestoppt ist")
    catalog = sub.add_parser("build-sku-catalog", help="SKU-Katalog aus Microsofts CSV erzeugen")
    catalog.add_argument("csv", help="heruntergeladene Microsoft-CSV (siehe core/sku_catalog.py)")
    catalog.add_argument("out", help="Ausgabedatei, z. B. app/data/sku_catalog.yaml")
    for name, text in (
        ("user-add", "Benutzer anlegen (Passwort wird abgefragt, Wechsel beim Login Pflicht)"),
        ("user-reset", "Passwort eines Benutzers zurücksetzen und Konto entsperren"),
    ):
        user_parser = sub.add_parser(name, help=text)
        user_parser.add_argument("username", help="Benutzername")
        if name == "user-add":
            user_parser.add_argument("--admin", action="store_true", help="mit Admin-Rolle anlegen")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command.startswith("external-backup-"):
        return external_backup_cmd(settings, args)
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
    if args.command == "cycle":
        return cycle_cmd(settings, args.slug)
    if args.command == "purge":
        return purge_cmd(settings)
    if args.command == "backup":
        return backup_cmd(settings)
    if args.command == "backup-list":
        return backup_list_cmd(settings)
    if args.command == "backup-verify":
        return backup_verify_cmd(args.file)
    if args.command == "restore-backup":
        return restore_backup_cmd(settings, args.file, args.yes)
    if args.command == "mail-check":
        return mail_check_cmd(settings)
    if args.command == "report-preview":
        return report_preview_cmd(settings, args.slug)
    if args.command == "report-send":
        return report_send_cmd(settings, args.slug)
    if args.command == "build-sku-catalog":
        return build_sku_catalog_cmd(args.csv, args.out)
    if args.command == "user-list":
        return user_list_cmd(settings)
    if args.command in ("user-add", "user-reset"):
        return user_write_cmd(
            settings,
            args.username,
            reset=args.command == "user-reset",
            admin=bool(getattr(args, "admin", False)),
        )
    return sync_entra_cmd(settings, args.slug)


if __name__ == "__main__":
    sys.exit(main())
