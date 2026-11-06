"""Befehle für den Wochenbericht: ``report-preview``, ``report-send``, ``mail-check``.

Ausgaben nennen nur Zähler und feste Fehlercodes, nie Empfängeradressen.
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import Settings
from app.core.report import ReportError, build_report, send_tenant_report
from app.db.models import Tenant, utcnow
from app.db.session import create_db_engine, make_session_factory
from app.mail.transport import MailError, check_connection


def mail_check_cmd(settings: Settings) -> int:
    """Prüft Verbindung, Zertifikat und Anmeldung beim Mailserver (sendet nichts)."""
    if settings.mail is None:
        print("FEHLER: Mailversand nicht eingerichtet (KW_SMTP_HOST fehlt).")
        return 1
    try:
        check_connection(settings.mail, settings.secrets_dir)
    except MailError as err:
        print(f"FEHLER: Mailserver nicht nutzbar ({err.code}).")
        return 1
    print("Mailserver erreichbar, Zertifikat und Anmeldung in Ordnung.")
    return 0


def report_preview_cmd(settings: Settings, slug: str) -> int:
    """Gibt den Bericht eines Mandanten aus, ohne ihn zu senden."""
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
            if tenant is None:
                print("FEHLER: Mandant unbekannt.")
                return 1
            report = build_report(session, tenant, utcnow())
    finally:
        engine.dispose()
    print(f"Betreff: {report.subject}")
    print(f"Empfänger: {len(report.recipients)}")
    print()
    print(report.text, end="")
    return 0


def report_send_cmd(settings: Settings, slug: str) -> int:
    """Sendet den Bericht eines Mandanten sofort."""
    engine = create_db_engine(settings.database_url)
    try:
        count = send_tenant_report(make_session_factory(engine), settings, slug, actor="cli")
    except (ReportError, MailError) as err:
        print(f"FEHLER: Bericht nicht gesendet ({err.code}).")
        return 1
    finally:
        engine.dispose()
    print(f"Bericht gesendet an {count} Empfänger.")
    return 0
