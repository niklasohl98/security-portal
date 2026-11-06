"""Seiten der Prüfliste verschwundener Identitäten (ADR 0006).

``GET /review`` listet offene und behaltene Einträge je Mandant. Entscheiden per POST; „Löschen“
führt über eine Bestätigungsseite (ohne JavaScript). Alle Änderungen betreffen nur diese
Datenbank, nie Entra oder AD.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.review import (
    Decision,
    MissingItem,
    ReviewError,
    decide,
    get_missing,
    list_missing,
)
from app.db.models import Tenant
from app.web.labels import account_type_label, format_dt, status_label
from app.web.security import form_fields, require_same_origin

log = logging.getLogger(__name__)

# Bis die Anmeldung steht (Phase 5), ist der Handelnde nicht bekannt.
ACTOR_UNAUTHENTICATED = "lokal (ohne Anmeldung)"

# Meldungen nur aus dieser Liste, nie aus Eingaben (kein Reflektieren von Parametern).
_MESSAGES = {
    "keep": "Eintrag wird behalten.",
    "delete": "Eintrag und seine Ausnahmen wurden gelöscht.",
    "reopen": "Eintrag steht wieder zur Prüfung an.",
    "error": "Nicht möglich: Der Eintrag ist nicht mehr vorhanden oder schon entschieden.",
}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["dt"] = format_dt
templates.env.filters["status_label"] = status_label
templates.env.filters["type_label"] = account_type_label

router = APIRouter()


@dataclass(frozen=True)
class TenantSection:
    """Eine Mandantenzeile der Prüfliste."""

    slug: str
    name: str
    open_items: list[MissingItem]
    kept_items: list[MissingItem]


def _session(request: Request) -> Session:
    session: Session = request.app.state.session_factory()
    return session


def _tenant_by_slug(session: Session, slug: str) -> Tenant:
    tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Mandant unbekannt")
    return tenant


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, done: str | None = None) -> Response:
    """Prüfliste: offene und behaltene verschwundene Identitäten aller aktiven Mandanten."""
    with _session(request) as session:
        tenants = session.scalars(
            select(Tenant).where(Tenant.enabled.is_(True)).order_by(Tenant.slug)
        ).all()
        sections = [
            TenantSection(
                t.slug,
                t.name,
                list_missing(session, t),
                list_missing(session, t, decided=True),
            )
            for t in tenants
        ]
    return templates.TemplateResponse(
        request,
        "review.html",
        {"sections": sections, "message": _MESSAGES.get(done or "")},
    )


@router.get("/review/{slug}/{identity_id}/delete", response_class=HTMLResponse)
def confirm_delete(request: Request, slug: str, identity_id: int) -> Response:
    """Bestätigungsseite vor dem Löschen (die Seite selbst ändert nichts)."""
    with _session(request) as session:
        tenant = _tenant_by_slug(session, slug)
        item = get_missing(session, tenant, identity_id)
        name = tenant.name
    if item is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    return templates.TemplateResponse(
        request, "review_confirm.html", {"slug": slug, "tenant_name": name, "item": item}
    )


@router.post("/review/{slug}/{identity_id}", dependencies=[Depends(require_same_origin)])
def post_decision(
    request: Request,
    slug: str,
    identity_id: int,
    fields: Annotated[dict[str, str], Depends(form_fields)],
) -> RedirectResponse:
    """Setzt eine Entscheidung (``keep``, ``delete``, ``reopen``) um und kehrt zur Liste zurück."""
    try:
        decision = Decision(fields.get("decision", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Entscheidung ungültig") from None
    outcome = decision.value
    with _session(request) as session, session.begin():
        tenant = _tenant_by_slug(session, slug)
        try:
            decide(session, tenant, identity_id, decision, ACTOR_UNAUTHENTICATED)
        except ReviewError as exc:
            log.info("Prüfentscheidung abgelehnt tenant_id=%s grund=%s", tenant.id, exc.code)
            outcome = "error"
    return RedirectResponse(f"/review?done={outcome}", status_code=303)
