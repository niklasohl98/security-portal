"""Angemeldeter Benutzer der aktuellen Anfrage und Werte für die Templates."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

# Name des Sitzungs-Cookies. Es enthält nur das zufällige Token (kein Benutzername, keine Rechte).
COOKIE_NAME = "kw_session"


@dataclass(frozen=True)
class AuthContext:
    """Die gültige Sitzung der Anfrage (losgelöst von der Datenbank)."""

    session_id: int
    user_id: int
    username: str
    must_change: bool
    csrf_token: str


def get_auth(request: Request) -> AuthContext | None:
    """Der angemeldete Benutzer oder ``None``; von der Middleware gesetzt."""
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    return ctx


def actor_name(request: Request) -> str:
    """Name für das Audit-Log. Ohne Anmeldung (darf bei geschützten Routen nicht vorkommen)
    steht ``unbekannt``."""
    ctx = get_auth(request)
    return ctx.username if ctx else "unbekannt"


def template_context(request: Request) -> dict[str, object]:
    """Werte für jedes Template: CSRF-Token, Benutzername, Zwang zum Passwortwechsel."""
    ctx = get_auth(request)
    return {
        "csrf_token": ctx.csrf_token if ctx else "",
        "current_user": ctx.username if ctx else None,
        "must_change": ctx.must_change if ctx else False,
    }
