"""Wochenbericht je Mandant: nur Kennzahlen, keine Konten.

Die Mail enthält bewusst keine Namen, UPNs oder Adressen von Konten (sensible Daten gehören nicht in
Postfächer); Einzelheiten stehen in der Oberfläche. Das Audit-Log nennt nur die Zahl der Empfänger
oder den Fehlercode, nie eine Adresse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.accounts import load_accounts
from app.core.overview import TenantOverview, tenant_overview
from app.db.models import AuditLog, Tenant, utcnow
from app.mail.transport import MailError, build_message, send_message
from app.web.labels import format_dt, run_status_label, warning_label

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=2)
_SOURCE_NAMES = {"entra": "Entra", "ad": "AD"}


class ReportError(Exception):
    """Bericht nicht sendbar. ``code``: ``not_configured``, ``unknown_tenant``, ``no_recipients``.

    Kein Mailversand eingerichtet, Mandant unbekannt oder ohne Empfänger.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Report:
    """Ein fertiger Bericht."""

    tenant_slug: str
    recipients: list[str]
    subject: str
    text: str


def _stale(overview: TenantOverview, now: datetime) -> bool:
    runs = overview.latest_runs
    if not runs:
        return True
    return any(now - (r.finished_at or r.started_at) > STALE_AFTER for r in runs)


def _lines(tenant: Tenant, ov: TenantOverview, now: datetime) -> list[str]:
    out = [
        f"Kontenwächter: Wochenbericht {tenant.name}",
        f"Stand: {format_dt(now)}",
        "",
    ]
    if _stale(ov, now):
        out += ["ACHTUNG: Die Daten sind älter als zwei Tage oder fehlen. Läufe prüfen.", ""]
    out += [
        f"Konten gesamt: {ov.total}",
        f"Ohne Anmeldung seit mehr als {tenant.inactive_days} Tagen (markiert): {ov.flagged}",
        f"  davon mit bezahlter Lizenz: {ov.flagged_licensed}",
    ]
    if ov.releasing_licenses:
        out.append("Lizenzen, die bei Bereinigung der markierten Konten frei würden:")
        out += [f"  - {name}: {count}" for name, count in ov.releasing_licenses.items()]
    out += [
        f"Verschwundene Konten zur Prüfung: {ov.missing_open}",
        f"Ausnahmen, die innerhalb von {tenant.exception_warn_days} Tagen ablaufen: "
        f"{ov.exceptions_expiring}",
        f"Abgelaufene Ausnahmen: {ov.exceptions_expired}",
        "",
        "Letzter Abruf:",
    ]
    for run in ov.latest_runs:
        source = _SOURCE_NAMES.get(run.source.value, run.source.value)
        when = format_dt(run.finished_at or run.started_at)
        out.append(f"  {source}: {run_status_label(run.status)}, {when}")
    if ov.warnings:
        out += ["", "Hinweise:"]
        out += [f"  - {warning_label(code)}" for code in ov.warnings]
    out += [
        "",
        "Einzelne Konten stehen bewusst nicht in dieser Mail. Sie sind im Kontenwächter einsehbar.",
    ]
    return out


def build_report(session: Session, tenant: Tenant, now: datetime) -> Report:
    """Baut den Bericht eines Mandanten aus der Datenbank (ändert nichts)."""
    rows = load_accounts(session, tenant, now)
    ov = tenant_overview(session, tenant, rows, now)
    return Report(
        tenant_slug=tenant.slug,
        recipients=[str(a) for a in tenant.report_recipients or []],
        subject=f"Kontenwächter Wochenbericht {tenant.name}",
        text="\n".join(_lines(tenant, ov, now)) + "\n",
    )


def _audit(
    factory: sessionmaker[Session], tenant_id: int, actor: str, action: str, detail: str
) -> None:
    with factory.begin() as session:
        session.add(
            AuditLog(tenant_id=tenant_id, at=utcnow(), actor=actor, action=action, detail=detail)
        )


def send_tenant_report(
    factory: sessionmaker[Session],
    settings: Settings,
    slug: str,
    actor: str,
    now: datetime | None = None,
) -> int:
    """Sendet den Bericht des Mandanten an dessen Empfänger. Gibt die Zahl der Empfänger zurück.

    Raises:
        ReportError: ``not_configured``, ``unknown_tenant`` oder ``no_recipients``.
        MailError: Versand fehlgeschlagen (Code im Audit-Log als ``report.failed``).
    """
    if settings.mail is None:
        raise ReportError("not_configured")
    now = now or utcnow()
    with factory() as session:
        tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
        if tenant is None:
            raise ReportError("unknown_tenant")
        tenant_id = tenant.id
        report = build_report(session, tenant, now)
    if not report.recipients:
        raise ReportError("no_recipients")
    mail = build_message(report.recipients, report.subject, report.text)
    try:
        send_message(settings.mail, settings.secrets_dir, mail)
    except MailError as err:
        detail = f"error={err.code}" + (f" graph={err.graph_code}" if err.graph_code else "")
        _audit(factory, tenant_id, actor, "report.failed", detail)
        log.warning("Bericht nicht gesendet: Mandant=%s %s", slug, detail)
        raise
    _audit(factory, tenant_id, actor, "report.sent", f"recipients={len(report.recipients)}")
    log.info("Bericht gesendet: Mandant=%s Empfänger=%d", slug, len(report.recipients))
    return len(report.recipients)


def run_weekly_reports(factory: sessionmaker[Session], settings: Settings) -> None:
    """Zeitplan-Job: sendet den Bericht jedes aktiven Mandanten mit Empfängern.

    Ein Fehler bei einem Mandanten stoppt die anderen nicht.
    """
    if settings.mail is None:
        return
    with factory() as session:
        slugs = list(
            session.scalars(
                select(Tenant.slug).where(Tenant.enabled.is_(True)).order_by(Tenant.slug)
            )
        )
    for slug in slugs:
        try:
            send_tenant_report(factory, settings, slug, actor="system")
        except ReportError as err:
            if err.code != "no_recipients":
                log.warning("Bericht übersprungen: Mandant=%s Grund=%s", slug, err.code)
        except MailError:
            pass  # schon protokolliert und im Audit-Log
