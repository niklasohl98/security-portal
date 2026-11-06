"""Seite „Läufe“: Protokoll der Abrufe je Mandant und Quelle, Knopf „Jetzt abrufen“."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.cycle import is_cycle_busy, run_tenant_cycle
from app.core.overview import list_runs
from app.db.models import AuditLog, utcnow
from app.web.common import (
    ACTOR_UNAUTHENTICATED,
    db_session,
    enabled_tenants,
    templates,
    tenant_or_404,
)
from app.web.security import require_same_origin

RUN_LIMIT = 100

_DONE = {
    "started": "Abruf gestartet. Das Protokoll aktualisiert sich nach dem Neuladen der Seite.",
    "busy": "Für diesen Mandanten läuft bereits ein Abruf.",
    "unknown": "Mandant unbekannt oder deaktiviert.",
}

router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, done: str | None = None) -> Response:
    """Protokoll der jüngsten Läufe aller aktiven Mandanten, neueste zuerst."""
    with db_session(request) as session:
        tenants = enabled_tenants(session)
        runs = list_runs(session, tenants, RUN_LIMIT)
        names = [(t.slug, t.name, is_cycle_busy(t.slug)) for t in tenants]
    return templates.TemplateResponse(
        request,
        "runs.html",
        {"runs": runs, "tenants": names, "message": _DONE.get(done or "")},
    )


def _run_cycle(factory: sessionmaker[Session], settings: Settings, slug: str) -> None:
    run_tenant_cycle(factory, settings, slug)


@router.post("/runs/{slug}/start", dependencies=[Depends(require_same_origin)])
def start_run(request: Request, slug: str, background: BackgroundTasks) -> RedirectResponse:
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
                actor=ACTOR_UNAUTHENTICATED,
                action="sync.manual",
                detail=f"tenant_id={tenant.id}",
            )
        )
    background.add_task(
        _run_cycle, request.app.state.session_factory, request.app.state.settings, slug
    )
    return RedirectResponse("/runs?done=started", status_code=303)
