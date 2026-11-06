"""Zugriffsschutz: ohne gültige Sitzung kommt man nur auf ``/login``, ``/health`` und ``/static``.

Die Middleware lädt die Sitzung aus dem Cookie (``kw_session``) und legt den Benutzer in
``request.state.auth``. Muss das Passwort geändert werden (``must_change``), sind nur ``/password``
und ``/logout`` erreichbar; alles andere führt dorthin. Unangemeldete GET-Anfragen werden zu
``/login?next=…`` umgeleitet, alle anderen Anfragen bekommen 401.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session, sessionmaker

from app.auth.context import COOKIE_NAME, AuthContext
from app.auth.service import load_session
from app.config import Settings
from app.db.models import utcnow

PUBLIC_PATHS = frozenset({"/health", "/login"})
CHANGE_PASSWORD_PATHS = frozenset({"/password", "/logout"})
_SAFE_METHODS = ("GET", "HEAD")


def _lookup(factory: sessionmaker[Session], token: str) -> AuthContext | None:
    with factory() as session:
        info = load_session(session, token, utcnow())
    if info is None:
        return None
    return AuthContext(
        info.session_id, info.user_id, info.username, info.must_change, info.csrf_token
    )


def set_session_cookie(
    response: Response, request: Request, settings: Settings, token: str
) -> None:
    """Setzt das Sitzungs-Cookie: ``HttpOnly``, ``SameSite=Lax``, ``Secure`` bei HTTPS."""
    secure = settings.cookie_secure
    if secure is None:
        secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Löscht das Sitzungs-Cookie."""
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")


def add_auth(app: FastAPI) -> None:
    """Hängt den Zugriffsschutz an. Muss vor den Sicherheits-Headern hinzugefügt werden, damit
    auch Umleitungen und 401-Antworten deren Header tragen."""

    @app.middleware("http")
    async def _auth(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        token = request.cookies.get(COOKIE_NAME, "")
        skip_lookup = path == "/health" or path.startswith("/static/") or not token
        ctx = None
        if not skip_lookup:
            ctx = await run_in_threadpool(_lookup, request.app.state.session_factory, token)
        request.state.auth = ctx
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)
        if ctx is None:
            if request.method in _SAFE_METHODS:
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
            return PlainTextResponse("Anmeldung erforderlich", status_code=401)
        if ctx.must_change and path not in CHANGE_PASSWORD_PATHS:
            if request.method in _SAFE_METHODS:
                return RedirectResponse("/password", status_code=303)
            return PlainTextResponse("Passwortwechsel erforderlich", status_code=403)
        return await call_next(request)
