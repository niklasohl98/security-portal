"""Seite „Berichte“ (nur Admins): Wochenbericht ansehen, jetzt senden, Mailverbindung prüfen.

Die Vorschau zeigt denselben Text wie die Mail (nur Kennzahlen). Meldungen kommen aus festen
Listen; Fehler nennen nur einen Code, nie Adressen oder Servertexte.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.context import actor_name
from app.config import Settings
from app.core.report import ReportError, build_report, send_tenant_report
from app.db.models import utcnow
from app.mail.settings import WEEKDAY_LABELS
from app.mail.transport import MailError, check_connection
from app.web.common import db_session, enabled_tenants, templates, tenant_or_404
from app.web.security import form_fields, require_admin, require_same_origin

_DONE = {
    "sent": "Bericht gesendet.",
    "check_ok": "Verbindung, Zertifikat und Anmeldung beim Mailserver sind in Ordnung.",
}
_ERRORS = {
    "not_configured": "Der Mailversand ist nicht eingerichtet (KW_SMTP_HOST, siehe README).",
    "no_recipients": "Für diesen Mandanten sind keine Empfänger eingetragen (report.recipients).",
    "unknown_tenant": "Mandant unbekannt.",
    "secret": "Das Passwort-Secret für den Mailserver fehlt oder ist nicht lesbar.",
    "tls": "TLS-Fehler: Zertifikat des Mailservers nicht vertrauenswürdig oder Name passt nicht.",
    "connect": "Der Mailserver ist nicht erreichbar oder lehnt die Verbindung ab.",
    "auth": "Anmeldung beim Mailserver abgelehnt (Benutzer oder Passwort).",
    "recipients": "Der Mailserver hat alle Empfänger abgelehnt.",
    "send": "Der Mailserver hat die Mail nicht angenommen.",
}

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, done: str | None = None, err: str | None = None) -> Response:
    """Mailstatus, Mandanten mit Empfängerzahl und Vorschau auf Wunsch."""
    settings: Settings = request.app.state.settings
    mail = settings.mail
    with db_session(request) as session:
        tenants = [
            (t.slug, t.name, len(t.report_recipients or [])) for t in enabled_tenants(session)
        ]
    schedule = (
        f"{WEEKDAY_LABELS[mail.report_weekday]} {mail.report_time} ({settings.timezone})"
        if mail
        else None
    )
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "mail": mail,
            "schedule": schedule,
            "tenants": tenants,
            "message": _DONE.get(done or ""),
            "error": _ERRORS.get(err or ""),
        },
    )


@router.get("/reports/{slug}", response_class=HTMLResponse)
def report_preview(request: Request, slug: str) -> Response:
    """Vorschau des Berichts eines Mandanten (Text der Mail)."""
    with db_session(request) as session:
        tenant = tenant_or_404(session, slug)
        report = build_report(session, tenant, utcnow())
    return templates.TemplateResponse(
        request,
        "report_preview.html",
        {
            "slug": slug,
            "subject": report.subject,
            "text": report.text,
            "count": len(report.recipients),
        },
    )


@router.post("/reports/check", dependencies=[Depends(require_same_origin)])
def check_mail(
    request: Request, _fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Prüft Verbindung, Zertifikat und Anmeldung beim Mailserver, ohne eine Mail zu senden."""
    settings: Settings = request.app.state.settings
    if settings.mail is None:
        return RedirectResponse("/reports?err=not_configured", status_code=303)
    try:
        check_connection(settings.mail, settings.secrets_dir)
    except MailError as err:
        return RedirectResponse(f"/reports?err={err.code}", status_code=303)
    return RedirectResponse("/reports?done=check_ok", status_code=303)


@router.post("/reports/{slug}/send", dependencies=[Depends(require_same_origin)])
def send_report(
    request: Request, slug: str, _fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Sendet den Bericht des Mandanten sofort an dessen Empfänger."""
    settings: Settings = request.app.state.settings
    try:
        send_tenant_report(
            request.app.state.session_factory, settings, slug, actor=actor_name(request)
        )
    except ReportError as err:
        return RedirectResponse(f"/reports?err={err.code}", status_code=303)
    except MailError as err:
        return RedirectResponse(f"/reports?err={err.code}", status_code=303)
    return RedirectResponse("/reports?done=sent", status_code=303)
