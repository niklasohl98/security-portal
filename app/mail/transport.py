"""Mailversand über Microsoft Graph (``POST /users/{postfach}/sendMail``, Exchange Online).

Eigene App-Registrierung im Softchem-Tenant, getrennt vom lesenden Zugang zu den Kunden-Tenants
(``connectors/graph.py`` bleibt rein lesend). Anmeldung per Zertifikat, Token über dieselbe
``MsalCertificateTokenProvider``-Klasse. Fehler tragen nur einen festen Code und, wenn Graph ihn
nennt, dessen technischen Fehlercode (z. B. ``ErrorAccessDenied``), nie Meldungstexte: die können
Adressen enthalten.

Es gibt keine automatische Wiederholung: ``sendMail`` ist nicht idempotent, eine Wiederholung nach
einem unklaren Fehler könnte doppelt senden. Der Wochenjob protokolliert den Fehler; „Jetzt senden“
wiederholt von Hand.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import ConfigError, read_secret
from app.connectors.graph import (
    GRAPH_BASE,
    GraphError,
    MsalCertificateTokenProvider,
    TokenProvider,
)
from app.mail.settings import MailSettings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
_GRAPH_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_STATUS_CODES = {
    400: "rejected",
    401: "auth",
    403: "forbidden",
    404: "sender",
    429: "throttled",
}


class MailError(Exception):
    """Versand nicht möglich.

    ``code``: ``secret`` (Zertifikat-Secret fehlt), ``auth`` (Token nicht erhältlich oder 401),
    ``forbidden`` (403: Berechtigung oder Exchange-Bereich fehlt), ``sender`` (404: Postfach
    unbekannt oder nicht für Graph aktiviert), ``rejected`` (400), ``throttled`` (429),
    ``connect`` (Netzwerk), ``send`` (sonstiger Fehler). ``graph_code`` ist der technische
    Fehlercode von Graph, falls vorhanden.
    """

    def __init__(self, code: str, graph_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.graph_code = graph_code


@dataclass(frozen=True)
class OutgoingMail:
    """Eine Textmail (Betreff ohne Zeilenumbrüche)."""

    recipients: list[str]
    subject: str
    text: str


def build_message(recipients: list[str], subject: str, text: str) -> OutgoingMail:
    """Baut eine Textmail; der Betreff verliert Zeilenumbrüche."""
    return OutgoingMail(list(recipients), " ".join(subject.split()), text)


def make_token_provider(settings: MailSettings, secrets_dir: Path) -> TokenProvider:
    """Token-Quelle der Mail-App (privater Schlüssel aus dem Docker-Secret).

    Raises:
        MailError: ``secret`` (nicht lesbar) oder ``auth`` (Schlüssel ungültig).
    """
    try:
        pem = read_secret(settings.certificate_secret, secrets_dir)
    except ConfigError:
        raise MailError("secret") from None
    try:
        return MsalCertificateTokenProvider(
            settings.tenant_id, settings.client_id, pem, settings.certificate_thumbprint
        )
    except (ValueError, OSError):
        raise MailError("auth") from None


def _token(tokens: TokenProvider) -> str:
    try:
        return tokens.get_token()
    except GraphError:
        raise MailError("auth") from None
    except (httpx.HTTPError, OSError):
        raise MailError("connect") from None


def check_connection(
    settings: MailSettings, secrets_dir: Path, *, tokens: TokenProvider | None = None
) -> None:
    """Prüft Zertifikat, App und Tenant durch das Holen eines Tokens (sendet nichts).

    Ob das Postfach senden darf, zeigt erst der erste Versand (Exchange prüft dort).

    Raises:
        MailError: ``secret``, ``auth`` oder ``connect``.
    """
    _token(tokens or make_token_provider(settings, secrets_dir))


def _graph_code(resp: httpx.Response) -> str | None:
    try:
        code = resp.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and _GRAPH_CODE.fullmatch(code) else None


def _payload(mail: OutgoingMail) -> dict[str, object]:
    return {
        "message": {
            "subject": mail.subject,
            "body": {"contentType": "Text", "content": mail.text},
            "toRecipients": [{"emailAddress": {"address": a}} for a in mail.recipients],
        },
        "saveToSentItems": False,
    }


def send_message(
    settings: MailSettings,
    secrets_dir: Path,
    mail: OutgoingMail,
    *,
    tokens: TokenProvider | None = None,
    http: httpx.Client | None = None,
) -> None:
    """Sendet die Mail als ``settings.sender``; Graph antwortet bei Erfolg mit 202.

    Raises:
        MailError: mit festem Code (siehe ``MailError``).
    """
    token = _token(tokens or make_token_provider(settings, secrets_dir))
    url = f"{GRAPH_BASE}/users/{quote(settings.sender, safe='@')}/sendMail"
    client = http or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        resp = client.post(url, json=_payload(mail), headers={"Authorization": f"Bearer {token}"})
    except httpx.TransportError:
        raise MailError("connect") from None
    finally:
        if http is None:
            client.close()
    if resp.status_code < 300:
        return
    raise MailError(_STATUS_CODES.get(resp.status_code, "send"), _graph_code(resp))
