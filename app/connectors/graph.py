"""Microsoft-Graph-Anbindung (nur lesend).

- Anmeldung per Zertifikat (Client-Credentials, ``msal``), kein Benutzerkontext.
- ``httpx`` mit Paging (``@odata.nextLink``), Wiederholung bei 429/502/503/504 mit ``Retry-After``
  bzw. exponentiellem Backoff.
- Fehler tragen nur Statuscode und Fehlercode, nie Meldungstexte oder Antwortinhalte (die
  könnten UPNs oder Namen enthalten).

Dieses Modul schreibt nie nach Entra: es gibt nur ``GET``-Aufrufe.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import msal

log = logging.getLogger(__name__)

GRAPH_HOST = "graph.microsoft.com"
GRAPH_BASE = f"https://{GRAPH_HOST}/v1.0"
GRAPH_SCOPE = f"https://{GRAPH_HOST}/.default"

_BASE_FIELDS = (
    "id,displayName,userPrincipalName,mail,userType,accountEnabled,createdDateTime,"
    "onPremisesSyncEnabled,onPremisesImmutableId,onPremisesSamAccountName,"
    "assignedLicenses,licenseAssignmentStates"
)
# Mit signInActivity ist die Seitengröße auf 500 begrenzt (Graph-Doku, List users).
_PAGE_SIZE = 999
_PAGE_SIZE_SIGN_IN = 500
_RETRY_STATUS = frozenset({429, 502, 503, 504})
_MAX_RETRY_AFTER = 300.0


class GraphError(Exception):
    """Fehler beim Graph-Aufruf. ``str()`` enthält nur Status und Fehlercode."""

    def __init__(self, status: int | None = None, code: str | None = None) -> None:
        super().__init__(f"Graph-Fehler: status={status} code={code}")
        self.status = status
        self.code = code


class TokenProvider(Protocol):
    """Liefert ein Zugriffstoken für Graph."""

    def get_token(self) -> str:
        """Gibt ein gültiges Bearer-Token zurück (Caching ist Sache des Providers)."""
        ...


class MsalCertificateTokenProvider:
    """Token per Client-Credentials-Flow mit Zertifikat (privater Schlüssel als PEM)."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        private_key_pem: str,
        thumbprint: str,
        *,
        app_factory: Callable[..., Any] = msal.ConfidentialClientApplication,
    ) -> None:
        """Erzeugt die MSAL-Anwendung. ``msal`` cached Token und erneuert sie selbst."""
        self._app = app_factory(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential={"private_key": private_key_pem, "thumbprint": thumbprint},
            timeout=30,
        )

    def get_token(self) -> str:
        """Holt das Token; Fehlertext von MSAL wird verworfen, nur der Fehlercode bleibt."""
        result = self._app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str):
            code = result.get("error") if isinstance(result, dict) else None
            raise GraphError(code=f"auth:{code}" if isinstance(code, str) else "auth")
        return token


def _error_code(resp: httpx.Response) -> str | None:
    """Liest ``error.code`` aus einer Graph-Fehlerantwort (nie die Meldung)."""
    try:
        code = resp.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and len(code) <= 100 else None


def _retry_after(resp: httpx.Response) -> float | None:
    """``Retry-After`` in Sekunden (Graph liefert Sekunden); ungültige Werte -> ``None``."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), _MAX_RETRY_AFTER)
    except ValueError:
        return None


class GraphClient:
    """Lesender Graph-Client mit Paging und Wiederholung."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        http: httpx.Client | None = None,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Args:
        tokens: Token-Quelle.
        http: Eigener ``httpx.Client`` (Tests); Standard ist ein Client mit 30 s Zeitlimit.
        max_retries: Wiederholungen je Anfrage bei transienten Fehlern.
        backoff_base: Startwert des exponentiellen Backoffs in Sekunden (ohne ``Retry-After``).
        backoff_cap: Obergrenze des Backoffs in Sekunden.
        sleep: Wartefunktion (in Tests ersetzbar).
        """
        self._tokens = tokens
        self._http = http or httpx.Client(timeout=30.0)
        self._max_retries = max_retries
        self._base = backoff_base
        self._cap = backoff_cap
        self._sleep = sleep

    def _wait(self, attempt: int, resp: httpx.Response | None) -> None:
        delay = _retry_after(resp) if resp is not None else None
        if delay is None:
            delay = min(self._cap, self._base * 2**attempt)
        self._sleep(delay)

    def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """``GET`` mit Wiederholung bei 429/502/503/504 und Netzwerkfehlern.

        Raises:
            GraphError: Nicht-2xx-Antwort ohne weitere Wiederholung oder Versuche erschöpft.
        """
        for attempt in range(self._max_retries + 1):
            headers = {"Authorization": f"Bearer {self._tokens.get_token()}"}
            try:
                resp = self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise GraphError(code=f"network:{type(exc).__name__}") from None
                self._wait(attempt, None)
                continue
            if resp.status_code in _RETRY_STATUS and attempt < self._max_retries:
                log.warning("Graph antwortet %d, Wiederholung %d", resp.status_code, attempt + 1)
                self._wait(attempt, resp)
                continue
            if resp.status_code >= 300:
                raise GraphError(resp.status_code, _error_code(resp))
            body = resp.json()
            if not isinstance(body, dict):
                raise GraphError(resp.status_code, "invalid_body")
            return body
        raise AssertionError("unreachable")  # pragma: no cover

    def pages(
        self, path: str, params: dict[str, str] | None = None
    ) -> Iterator[list[dict[str, Any]]]:
        """Liefert die ``value``-Listen aller Seiten und folgt ``@odata.nextLink``.

        Der Link wird nur befolgt, wenn er auf ``https://graph.microsoft.com`` zeigt, sonst
        würde das Bearer-Token an einen fremden Host gesendet.
        """
        url: str | None = f"{GRAPH_BASE}{path}"
        first = True
        while url:
            body = self.get(url, params if first else None)
            first = False
            items = body.get("value", [])
            if not isinstance(items, list):
                raise GraphError(code="invalid_body")
            yield items
            nxt = body.get("@odata.nextLink")
            url = _checked_next_link(nxt) if nxt else None


def _checked_next_link(link: object) -> str:
    if not isinstance(link, str):
        raise GraphError(code="invalid_next_link")
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.hostname != GRAPH_HOST or parts.port not in (None, 443):
        raise GraphError(code="invalid_next_link")
    return link


# --- Datentypen und Abrufe -----------------------------------------------------------------


@dataclass(frozen=True)
class EntraUser:
    """Ein Entra-Konto, auf die benötigten Felder reduziert."""

    object_id: str
    user_principal_name: str
    display_name: str | None
    mail: str | None
    user_type: str | None
    account_enabled: bool | None
    created_at: datetime | None
    on_prem_sync_enabled: bool | None
    on_prem_immutable_id: str | None
    on_prem_sam_account_name: str | None
    last_successful_sign_in: datetime | None
    last_non_interactive_sign_in: datetime | None
    last_sign_in: datetime | None
    licenses: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class UsersResult:
    """Alle Konten eines Abrufs und ob Anmeldedaten verfügbar waren."""

    users: list[EntraUser]
    sign_in_available: bool


@dataclass(frozen=True)
class SkuInfo:
    """Ein Lizenzprodukt des Mandanten."""

    sku_id: str
    part_number: str
    applies_to: str | None
    capability_status: str | None
    enabled_units: int
    consumed_units: int


@dataclass(frozen=True)
class Organization:
    """Mandantenname und verifizierte Domänen (für den Verbindungstest)."""

    display_name: str | None
    domains: list[str]


def parse_graph_datetime(value: object) -> datetime | None:
    """ISO-8601-Zeitstempel von Graph -> UTC (timezone-aware); ungültig -> ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


_STATE_RANK = {"Error": 3, "ActiveWithError": 2, "Disabled": 1}


def _licenses(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Fasst ``assignedLicenses`` und ``licenseAssignmentStates`` je SKU zusammen.

    Ergebnis je SKU: ``direct`` (direkt zugewiesen), ``group`` (über Gruppe) und ``state``
    (schlechtester Zustand, sonst ``Active``).
    """
    by_sku: dict[str, dict[str, Any]] = {}
    for item in raw.get("assignedLicenses") or []:
        sku = item.get("skuId")
        if isinstance(sku, str):
            by_sku[sku] = {"sku_id": sku, "direct": False, "group": False, "state": "Active"}
    for st in raw.get("licenseAssignmentStates") or []:
        sku = st.get("skuId")
        if not isinstance(sku, str):
            continue
        entry = by_sku.setdefault(
            sku, {"sku_id": sku, "direct": False, "group": False, "state": "Active"}
        )
        if st.get("assignedByGroup"):
            entry["group"] = True
        else:
            entry["direct"] = True
        state = st.get("state")
        if isinstance(state, str) and _STATE_RANK.get(state, 0) > _STATE_RANK.get(
            entry["state"], 0
        ):
            entry["state"] = state
    return sorted(by_sku.values(), key=lambda e: e["sku_id"])


def parse_user(raw: dict[str, Any]) -> EntraUser:
    """Wandelt ein Graph-User-Objekt in ``EntraUser``."""
    activity = raw.get("signInActivity") or {}
    return EntraUser(
        object_id=str(raw["id"]),
        user_principal_name=str(raw.get("userPrincipalName") or ""),
        display_name=raw.get("displayName"),
        mail=raw.get("mail"),
        user_type=raw.get("userType"),
        account_enabled=raw.get("accountEnabled"),
        created_at=parse_graph_datetime(raw.get("createdDateTime")),
        on_prem_sync_enabled=raw.get("onPremisesSyncEnabled"),
        on_prem_immutable_id=raw.get("onPremisesImmutableId"),
        on_prem_sam_account_name=raw.get("onPremisesSamAccountName"),
        last_successful_sign_in=parse_graph_datetime(activity.get("lastSuccessfulSignInDateTime")),
        last_non_interactive_sign_in=parse_graph_datetime(
            activity.get("lastNonInteractiveSignInDateTime")
        ),
        last_sign_in=parse_graph_datetime(activity.get("lastSignInDateTime")),
        licenses=_licenses(raw),
    )


def _collect_users(client: GraphClient, *, with_sign_in: bool) -> list[EntraUser]:
    select = f"{_BASE_FIELDS},signInActivity" if with_sign_in else _BASE_FIELDS
    params = {"$select": select, "$top": str(_PAGE_SIZE_SIGN_IN if with_sign_in else _PAGE_SIZE)}
    seen: dict[str, EntraUser] = {}
    for page in client.pages("/users", params):
        for raw in page:
            user = parse_user(raw)
            seen.setdefault(user.object_id, user)  # Duplikate zwischen Seiten verwerfen
    return list(seen.values())


def fetch_users(client: GraphClient) -> UsersResult:
    """Liest alle Benutzer mit Lizenzen und (wenn möglich) Anmeldedaten.

    ``signInActivity`` braucht Entra ID P1/P2 und ``AuditLog.Read.All``. Antwortet Graph darauf
    mit 403, wird einmal ohne ``signInActivity`` wiederholt (``sign_in_available=False``). Ein
    zweiter 403 ist dann ein echter Berechtigungsfehler und wird weitergereicht.
    """
    try:
        return UsersResult(_collect_users(client, with_sign_in=True), True)
    except GraphError as exc:
        if exc.status != 403:
            raise
        log.warning("signInActivity nicht verfügbar (403, code=%s), Abruf ohne", exc.code)
    return UsersResult(_collect_users(client, with_sign_in=False), False)


def fetch_skus(client: GraphClient) -> list[SkuInfo]:
    """Liest den Lizenzbestand (``GET /subscribedSkus``)."""
    skus: list[SkuInfo] = []
    for page in client.pages("/subscribedSkus"):
        for raw in page:
            prepaid = raw.get("prepaidUnits") or {}
            skus.append(
                SkuInfo(
                    sku_id=str(raw["skuId"]),
                    part_number=str(raw.get("skuPartNumber") or ""),
                    applies_to=raw.get("appliesTo"),
                    capability_status=raw.get("capabilityStatus"),
                    enabled_units=int(prepaid.get("enabled") or 0),
                    consumed_units=int(raw.get("consumedUnits") or 0),
                )
            )
    return skus


def fetch_organization(client: GraphClient) -> Organization:
    """Liest Mandantenname und verifizierte Domänen (``GET /organization``)."""
    params = {"$select": "displayName,verifiedDomains"}
    for page in client.pages("/organization", params):
        for raw in page:
            domains = [
                d["name"]
                for d in raw.get("verifiedDomains") or []
                if isinstance(d.get("name"), str)
            ]
            return Organization(raw.get("displayName"), domains)
    return Organization(None, [])
