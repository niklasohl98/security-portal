"""Seiten: Anmeldung (``/login``), Abmeldung (``/logout``) und Passwortwechsel (``/password``).

Anmeldefehler zeigen immer denselben Text (kein Hinweis, ob der Benutzer existiert oder gesperrt
ist). Nach erfolgreicher Anmeldung gibt es ein neues Sitzungstoken (kein Wiederverwenden eines
vorhandenen). Das ``next``-Ziel ist nur ein Pfad auf dieser Seite (kein offener Redirect).
"""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import passwords, service
from app.auth.context import COOKIE_NAME, AuthContext, get_auth
from app.auth.middleware import clear_session_cookie, set_session_cookie
from app.auth.service import AuthOutcome
from app.config import Settings
from app.web.common import db_session, templates
from app.web.security import form_fields, read_form, require_same_origin

log = logging.getLogger(__name__)

LOGIN_FAILED = (
    "Anmeldung nicht möglich: Benutzername oder Passwort falsch, "
    "oder das Konto ist vorübergehend gesperrt."
)
_PASSWORD_ERRORS = {
    "wrong_current": "Das aktuelle Passwort ist nicht korrekt.",
    "locked": "Zu viele Fehlversuche. Bitte später erneut versuchen.",
    "mismatch": "Die beiden neuen Passwörter stimmen nicht überein.",
    "too_short": f"Das neue Passwort muss mindestens {passwords.MIN_LENGTH} Zeichen lang sein.",
    "too_long": f"Das neue Passwort darf höchstens {passwords.MAX_LENGTH} Zeichen lang sein.",
    "contains_username": "Das neue Passwort darf den Benutzernamen nicht enthalten.",
    "forbidden": "Dieses Passwort ist nicht erlaubt (bekanntes Standardpasswort).",
    "same_as_current": "Das neue Passwort muss sich vom aktuellen unterscheiden.",
}

router = APIRouter()


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def safe_next(target: str | None) -> str:
    """Ein Weiterleitungsziel nur, wenn es ein Pfad auf dieser Seite ist (sonst ``/``)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or any(ord(c) < 32 for c in target):
        return "/"
    if parts.path in ("/login", "/logout"):
        return "/"
    return target


def _login_page(
    request: Request, target: str, username: str, error: str | None, status: int
) -> Response:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": target, "username": username[:64], "error": error},
        status_code=status,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None) -> Response:
    """Anmeldeformular; wer schon angemeldet ist, wird weitergeleitet."""
    ctx = get_auth(request)
    target = safe_next(next)
    if ctx is not None:
        return RedirectResponse("/password" if ctx.must_change else target, status_code=303)
    return _login_page(request, target, "", None, 200)


@router.post("/login", dependencies=[Depends(require_same_origin)])
def login(request: Request, fields: Annotated[dict[str, str], Depends(read_form)]) -> Response:
    """Prüft die Anmeldedaten und setzt bei Erfolg ein neues Sitzungs-Cookie."""
    settings = _settings(request)
    username = fields.get("username", "")[:200]
    target = safe_next(fields.get("next"))
    with db_session(request) as session, session.begin():
        result = service.authenticate(session, settings, username, fields.get("password", ""))
        new = (
            service.create_session(session, settings, result.user_id)
            if result.outcome is AuthOutcome.OK and result.user_id is not None
            else None
        )
    if new is None:
        return _login_page(request, target, username, LOGIN_FAILED, 401)
    response = RedirectResponse("/password" if result.must_change else target, status_code=303)
    set_session_cookie(response, request, settings, new.token)
    return response


@router.post("/logout")
def logout(
    request: Request, _fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Beendet die Sitzung (CSRF-geschützt) und löscht das Cookie."""
    ctx = get_auth(request)
    token = request.cookies.get(COOKIE_NAME, "")
    with db_session(request) as session, session.begin():
        if ctx is not None:
            service.record_logout(
                session,
                service.SessionInfo(
                    ctx.session_id, ctx.user_id, ctx.username, ctx.must_change, ctx.csrf_token
                ),
            )
        service.destroy_session(session, token)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


def _password_page(
    request: Request, ctx: AuthContext, errors: list[str], status: int = 200
) -> Response:
    return templates.TemplateResponse(
        request,
        "password.html",
        {
            "forced": ctx.must_change,
            "errors": [_PASSWORD_ERRORS[c] for c in errors if c in _PASSWORD_ERRORS],
            "min_length": passwords.MIN_LENGTH,
        },
        status_code=status,
    )


@router.get("/password", response_class=HTMLResponse)
def password_page(request: Request) -> Response:
    """Formular zum Passwortwechsel (bei erzwungenem Wechsel die einzige erreichbare Seite)."""
    ctx = get_auth(request)
    assert ctx is not None  # noqa: S101 - die Middleware lässt nur angemeldete Benutzer durch
    return _password_page(request, ctx, [])


@router.post("/password")
def change_password(
    request: Request, fields: Annotated[dict[str, str], Depends(form_fields)]
) -> Response:
    """Ändert das Passwort. Danach gilt eine neue Sitzung; alle anderen enden."""
    ctx = get_auth(request)
    assert ctx is not None  # noqa: S101 - siehe oben
    settings = _settings(request)
    new_session = None
    with db_session(request) as session, session.begin():  # auch im Fehlerfall committen (Zähler)
        codes = service.change_password(
            session,
            settings,
            ctx.user_id,
            fields.get("current_password", ""),
            fields.get("new_password", ""),
            fields.get("confirm_password", ""),
        )
        if not codes:
            new_session = service.create_session(session, settings, ctx.user_id)
    if new_session is None:
        return _password_page(request, ctx, codes, 400)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, request, settings, new_session.token)
    return response
