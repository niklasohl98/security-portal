"""Entra-Anmeldung: browsergebundener Code-Flow und ausschließlich POST-Callback."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import service
from app.auth.middleware import set_session_cookie
from app.auth.sso_provider import SsoError
from app.auth.sso_runtime import FLOW_SECONDS, PendingFlow, SsoRuntime
from app.auth.sso_users import login_user
from app.web.auth_pages import safe_next
from app.web.common import db_session
from app.web.security import read_form

log = logging.getLogger(__name__)
router = APIRouter()
FLOW_COOKIE = "kw_sso_flow"
FLOW_PATH = "/auth/entra"


def _runtime(request: Request) -> SsoRuntime:
    runtime: SsoRuntime | None = request.app.state.sso
    if runtime is None:
        raise HTTPException(status_code=404, detail="Anmeldung nicht eingerichtet")
    return runtime


def _failed() -> RedirectResponse:
    response = RedirectResponse("/login?sso_error=1", status_code=303)
    response.delete_cookie(FLOW_COOKIE, path=FLOW_PATH, secure=True, httponly=True, samesite="none")
    return response


@router.get("/auth/entra/start")
def start(request: Request, next: str | None = None) -> RedirectResponse:
    """Erzeugt State/PKCE/Nonce serverseitig und bindet den Callback an diesen Browser."""
    runtime = _runtime(request)
    try:
        token, uri = runtime.begin(safe_next(next))
    except SsoError as exc:
        log.warning("SSO-Anmeldung nicht möglich code=%s", str(exc))
        return _failed()
    response = RedirectResponse(uri, status_code=303)
    response.set_cookie(
        FLOW_COOKIE,
        token,
        max_age=FLOW_SECONDS,
        path=FLOW_PATH,
        secure=True,
        httponly=True,
        samesite="none",
    )
    return response


@router.post("/auth/entra/callback")
def callback(
    request: Request,
    fields: Annotated[dict[str, str], Depends(read_form)],
) -> RedirectResponse:
    """Prüft den Einmal-Flow, signiertes Token und aktuelle Gruppen vor Sitzungserstellung."""
    runtime = _runtime(request)
    pending: PendingFlow | None = None
    new: service.NewSession | None = None
    transferred = False
    try:
        pending = runtime.consume(request.cookies.get(FLOW_COOKIE, ""))
        grant = pending.provider.complete(pending.flow, fields)
        admin = runtime.allowed(pending.provider, grant)
        cfg = request.app.state.settings
        with db_session(request) as session, session.begin():
            uid = login_user(session, runtime.cfg, grant.object_id, admin)
            new = service.create_session(session, cfg, uid, is_oidc=True)
            runtime.register(new.token, pending.provider, grant, admin, new.expires_at)
        transferred = True
        response = RedirectResponse(pending.target, status_code=303)
        response.delete_cookie(
            FLOW_COOKIE, path=FLOW_PATH, secure=True, httponly=True, samesite="none"
        )
        set_session_cookie(response, request, cfg, new.token)
        return response
    except SsoError as exc:
        log.warning("SSO-Anmeldung abgelehnt code=%s", str(exc))
        return _failed()
    finally:
        if not transferred:
            if new is not None:
                runtime.forget(new.token)
            if pending is not None:
                pending.provider.close()
