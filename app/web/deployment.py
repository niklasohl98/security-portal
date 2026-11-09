"""Feste öffentliche HTTPS-Adresse; Proxy-Header verarbeitet ausschließlich Uvicorn.

Die Anwendung vertraut nicht selbst auf Forwarded-Header. Uvicorn darf sie nur von ausdrücklich
konfigurierten Proxy-Adressen übernehmen. Der lokale Healthcheck bleibt ohne HTTPS erreichbar.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import ConfigError

_DNS_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def public_origin(value: str) -> str:
    """Validiert und normalisiert eine HTTPS-Origin, ohne Eingabewerte in Fehlern auszugeben."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
        if (
            any(c.isspace() or ord(c) < 32 for c in value)
            or parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
            or port == 0
            or parts.netloc.endswith(":")
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _DNS_NAME.fullmatch(host) or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                for label in host.split(".")
            ):
                raise ValueError from None
        authority = f"[{host}]" if ":" in host else host
        return f"https://{authority}" + (f":{port}" if port not in (None, 443) else "")
    except ValueError:
        raise ConfigError(
            "KW_PUBLIC_URL muss eine HTTPS-Adresse ohne Pfad oder Zugangsdaten sein"
        ) from None


def add_deployment_guard(app: FastAPI, public_url: str | None) -> None:
    """Lehnt bei gesetzter öffentlicher URL fremde Hosts und unverschlüsselten Zugang ab.

    ``/health`` (GET/HEAD) bleibt für den Container-Healthcheck erreichbar. Ein offener Backend-
    Port muss zusätzlich durch Bindung/Firewall gegen direkten Zugriff geschützt werden.
    """
    if public_url is None:
        return
    expected = public_origin(public_url)

    @app.middleware("http")
    async def _deployment(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health" and request.method in ("GET", "HEAD"):
            return await call_next(request)
        try:
            actual = public_origin(f"{request.url.scheme}://{request.headers.get('host', '')}")
        except ConfigError:
            return PlainTextResponse("HTTPS-Zugang erforderlich", status_code=400)
        if actual != expected:
            return PlainTextResponse("Ungültiger Host", status_code=400)
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
