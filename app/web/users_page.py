"""Seite „Benutzer“ (nur Admins): anlegen, deaktivieren, Rolle ändern, Passwort zurücksetzen.

Einmalpasswörter erscheinen nur in der Antwort auf das Anlegen oder Zurücksetzen (nie in der
Adresszeile, im Log oder im Audit-Log). Benutzer werden nie gelöscht, nur deaktiviert, damit das
Audit-Log eindeutig bleibt. Siehe ``auth/users.py`` für die Schutzregeln.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import users
from app.auth.context import actor_name, get_auth
from app.auth.service import UserError
from app.auth.users import UserAdminError
from app.web.common import db_session, templates
from app.web.security import form_fields, require_admin, require_same_origin

_DONE = {
    "active": "Benutzer aktiviert.",
    "inactive": "Benutzer deaktiviert; seine Sitzungen sind beendet.",
    "admin_on": "Admin-Rolle vergeben.",
    "admin_off": "Admin-Rolle entzogen.",
}
_ERRORS = {
    "external": "Passwort und Admin-Rolle dieses Kontos werden in Entra verwaltet.",
    "self": "Das eigene Konto lässt sich hier nicht ändern (Passwort: „Passwort ändern“).",
    "last_admin": "Es muss mindestens ein aktiver Admin bleiben.",
    "not_found": "Benutzer nicht gefunden.",
    "bad_name": "Benutzername: 3 bis 64 Zeichen (Buchstaben, Ziffern, Punkt, Strich, Unterstrich).",
    "exists": "Diesen Benutzernamen gibt es schon.",
}

router = APIRouter(dependencies=[Depends(require_admin)])


def _actor_id(request: Request) -> int:
    ctx = get_auth(request)
    return ctx.user_id if ctx else -1


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, done: str | None = None, err: str | None = None) -> Response:
    """Liste der Benutzer mit Aktionen und dem Formular zum Anlegen."""
    with db_session(request) as session:
        rows = users.list_user_rows(session)
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "rows": rows,
            "me": _actor_id(request),
            "message": _DONE.get(done or ""),
            "error": _ERRORS.get(err or ""),
        },
    )


def _secret_page(request: Request, username: str, password: str, created: bool) -> Response:
    return templates.TemplateResponse(
        request,
        "user_secret.html",
        {"username": username, "password": password, "created": created},
    )


@router.post("/users", dependencies=[Depends(require_same_origin)])
def create_user(
    request: Request, fields: Annotated[dict[str, str], Depends(form_fields)]
) -> Response:
    """Legt einen Benutzer an und zeigt sein Einmalpasswort (einmalig)."""
    name = fields.get("username", "").strip()
    try:
        with db_session(request) as session, session.begin():
            _id, password = users.create_with_temp_password(
                session, name, fields.get("is_admin") == "1", actor_name(request)
            )
    except UserError as exc:
        return RedirectResponse(f"/users?err={exc.code}", status_code=303)
    return _secret_page(request, name, password, created=True)


@router.post("/users/{user_id}/reset", dependencies=[Depends(require_same_origin)])
def reset_user(
    request: Request,
    user_id: int,
    _fields: Annotated[dict[str, str], Depends(form_fields)],
) -> Response:
    """Setzt das Passwort eines anderen Benutzers zurück und zeigt das Einmalpasswort."""
    try:
        with db_session(request) as session, session.begin():
            name, password = users.reset_with_temp_password(
                session, user_id, _actor_id(request), actor_name(request)
            )
    except UserAdminError as exc:
        return RedirectResponse(f"/users?err={exc.code}", status_code=303)
    return _secret_page(request, name, password, created=False)


@router.post("/users/{user_id}/active", dependencies=[Depends(require_same_origin)])
def toggle_active(
    request: Request, user_id: int, fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Aktiviert (``value=1``) oder deaktiviert (``value=0``) einen Benutzer."""
    active = fields.get("value") == "1"
    try:
        with db_session(request) as session, session.begin():
            users.set_active(session, user_id, active, _actor_id(request), actor_name(request))
    except UserAdminError as exc:
        return RedirectResponse(f"/users?err={exc.code}", status_code=303)
    return RedirectResponse(f"/users?done={'active' if active else 'inactive'}", status_code=303)


@router.post("/users/{user_id}/admin", dependencies=[Depends(require_same_origin)])
def toggle_admin(
    request: Request, user_id: int, fields: Annotated[dict[str, str], Depends(form_fields)]
) -> RedirectResponse:
    """Vergibt (``value=1``) oder entzieht (``value=0``) die Admin-Rolle."""
    admin = fields.get("value") == "1"
    try:
        with db_session(request) as session, session.begin():
            users.set_admin(session, user_id, admin, _actor_id(request), actor_name(request))
    except UserAdminError as exc:
        return RedirectResponse(f"/users?err={exc.code}", status_code=303)
    return RedirectResponse(f"/users?done={'admin_on' if admin else 'admin_off'}", status_code=303)
