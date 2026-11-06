"""Seite „Läufe“: Protokoll der Abrufe, Knopf „Jetzt abrufen“, Sicherungen und „Jetzt sichern“."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, sessionmaker

from app.auth.context import actor_name
from app.config import Settings
from app.core.backup import backup_status, list_backups, run_backup
from app.core.cycle import is_cycle_busy, run_tenant_cycle
from app.core.overview import list_runs
from app.db.models import AuditLog, utcnow
from app.web.common import (
    db_session,
    enabled_tenants,
    templates,
    tenant_or_404,
)
from app.web.security import form_fields, require_same_origin

RUN_LIMIT = 100

_DONE = {
    "started": "Abruf gestartet. Das Protokoll aktualisiert sich nach dem Neuladen der Seite.",
    "busy": "Für diesen Mandanten läuft bereits ein Abruf.",
    "unknown": "Mandant unbekannt oder deaktiviert.",
    "backup_ok": "Sicherung angelegt und geprüft.",
    "backup_failed": "Die Sicherung ist fehlgeschlagen (Verzeichnis eingehängt und beschreibbar?).",
}
BACKUP_LIST_LIMIT = 10

router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, done: str | None = None) -> Response:
    """Protokoll der jüngsten Läufe aller aktiven Mandanten, neueste zuerst."""
    settings: Settings = request.app.state.settings
    with db_session(request) as session:
        tenants = enabled_tenants(session)
        runs = list_runs(session, tenants, RUN_LIMIT)
        names = [(t.slug, t.name, is_cycle_busy(t.slug)) for t in tenants]
        status = backup_status(session, settings, utcnow())
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": runs,
            "tenants": names,
            "message": _DONE.get(done or ""),
            "backup": status,
            "backups": list_backups(settings.backup_dir)[:BACKUP_LIST_LIMIT],
            "backup_dir": str(settings.backup_dir),
            "keep_days": settings.backup_keep_days,
        },
    )


def _run_cycle(factory: sessionmaker[Session], settings: Settings, slug: str) -> None:
    run_tenant_cycle(factory, settings, slug)


@router.post("/runs/{slug}/start", dependencies=[Depends(require_same_origin)])
def start_run(
    request: Request,
    slug: str,
    background: BackgroundTasks,
    _fields: Annotated[dict[str, str], Depends(form_fields)],
) -> RedirectResponse:
    """Startet den Zyklus (Entra, AD, Auswertung) im Hintergrund und kehrt sofort zurück."""
    with db_session(request) as session, session.begin():
        tenant = tenant_or_404(session, slug)
        if not tenant.enabled:
            return RedirectResponse("/runs?done=unknown", status_code=303)
        if is_cycle_busy(slug):
            return RedirectResponse("/runs?done=busy", status_code=303)
        session.add(
            AuditLog(
                tenant_id=tenant.id,
                at=utcnow(),
                actor=actor_name(request),
                action="sync.manual",
                detail=f"tenant_id={tenant.id}",
            )
        )
    background.add_task(
        _run_cycle, request.app.state.session_factory, request.app.state.settings, slug
    )
    return RedirectResponse("/runs?done=started", status_code=303)


@router.post("/backup/start", dependencies=[Depends(require_same_origin)])
def start_backup(
    request: Request, _fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Sichert die Datenbank sofort (geprüft, mit Aufbewahrung); das Ergebnis steht im Audit-Log."""
    made = run_backup(
        request.app.state.session_factory, request.app.state.settings, actor_name(request)
    )
    return RedirectResponse(
        "/runs?done=" + ("backup_ok" if made else "backup_failed"), status_code=303
    )
