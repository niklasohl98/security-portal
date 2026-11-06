"""Sicherheitsbausteine der Oberfläche: Antwort-Header, Same-Origin-Prüfung, Formular-Parser.

Es gibt noch keine Anmeldung (KI-1, Phase 5). Bis dahin schützt nur die Bindung an
``127.0.0.1``; die Prüfung hier verhindert, dass eine fremde Webseite im Browser des Nutzers
Entscheidungen auslöst (CSRF). Kein JavaScript: die Content-Security-Policy erlaubt keines.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response

MAX_FORM_BYTES = 4096

_CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


def add_security_headers(app: FastAPI) -> None:
    """Setzt CSP und weitere Header; HTML-Seiten werden nicht zwischengespeichert."""

    @app.middleware("http")
    async def _headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Nicht "no-referrer": Dann sendet der Browser bei Formularen ``Origin: null`` und
        # ``require_same_origin`` würde die eigene Seite ablehnen (im echten Browser geprüft).
        response.headers["Referrer-Policy"] = "same-origin"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response


def require_same_origin(request: Request) -> None:
    """Weist Anfragen ab, die erkennbar von einer anderen Webseite stammen.

    Browser senden bei POST immer ``Origin``; ``Sec-Fetch-Site`` kommt zusätzlich bei neueren.
    Fehlen beide (Skripte wie curl), ist es kein Browser-CSRF und die Anfrage ist zulässig.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="Anfrage von fremder Herkunft")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Anfrage von fremder Herkunft")


async def form_fields(request: Request) -> dict[str, str]:
    """Liest ein ``application/x-www-form-urlencoded``-Formular (klein, ohne Zusatzpaket).

    Bei doppelten Feldern gilt das erste. Fehler nennen nie Feldwerte.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Formular erwartet")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(status_code=413, detail="Formular zu groß")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Formular ungültig") from None
    fields: dict[str, str] = {}
    for key, value in pairs:
        fields.setdefault(key, value)
    return fields
