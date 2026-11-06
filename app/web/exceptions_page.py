"""Seite „Ausnahmen“: alle Ausnahmen je Mandant, bald ablaufende hervorgehoben; Entfernen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.context import actor_name
from app.core.exceptions import ExceptionError, ExceptionItem, list_exceptions, revoke_exception
from app.db.models import utcnow
from app.web.common import (
    db_session,
    enabled_tenants,
    templates,
    tenant_or_404,
)
from app.web.security import form_fields, require_same_origin

_DONE = {"deleted": "Ausnahme entfernt.", "error": "Die Ausnahme gibt es nicht mehr."}

router = APIRouter()


@router.get("/exceptions", response_class=HTMLResponse)
def exceptions_page(request: Request, done: str | None = None) -> Response:
    """Liste mit Grund, Ersteller und Ablauf; abgelaufene und bald ablaufende sind markiert."""
    now = utcnow()
    sections: list[tuple[str, str, list[ExceptionItem]]] = []
    with db_session(request) as session:
        for tenant in enabled_tenants(session):
            sections.append((tenant.slug, tenant.name, list_exceptions(session, tenant, now)))
    return templates.TemplateResponse(
        request,
        "exceptions.html",
        {"sections": sections, "message": _DONE.get(done or "")},
    )


@router.post(
    "/exceptions/{slug}/{exception_id}/delete", dependencies=[Depends(require_same_origin)]
)
def delete_exception(
    request: Request,
    slug: str,
    exception_id: int,
    _fields: Annotated[dict[str, str], Depends(form_fields)],
) -> RedirectResponse:
    """Entfernt eine Ausnahme; das Konto wird danach wieder nach den Regeln bewertet."""
    outcome = "deleted"
    with db_session(request) as session, session.begin():
        tenant = tenant_or_404(session, slug)
        try:
            revoke_exception(session, tenant, exception_id, actor_name(request))
        except ExceptionError:
            outcome = "error"
    return RedirectResponse(f"/exceptions?done={outcome}", status_code=303)
