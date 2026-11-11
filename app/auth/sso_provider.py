"""Entra-Code-Flow mit MSAL, eigener JWT-Prüfung und lesender Gruppenabfrage.

MSAL 1.39 prüft ID-Token nicht vollständig. Deshalb prüfen wir zusätzlich Signatur,
Aussteller, Zielanwendung, Zeitangaben, Tenant und Nonce. Tokens/Cache bleiben nur im Speicher.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import jwt
import msal

from app.auth.sso_settings import SsoSettings
from app.config import ConfigError, read_secret
from app.connectors.graph import GraphError, _checked_next_link

SCOPES = ["User.Read"]


class SsoError(Exception):
    """Fester Fehlercode ohne externe Fehlermeldungen oder Tokeninhalte."""


class _MsalHttpClient:
    """Passt MSALs Byte-Felder an die strengere Formularcodierung von httpx an."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Reicht einen MSAL-GET an den gemeinsamen Client weiter."""
        return self.client.get(url, **kwargs)

    def post(self, url: str, *, data: Any = None, **kwargs: Any) -> httpx.Response:
        """Decodiert ASCII-Bytes, damit eine JWT-Assertion nicht als ``b'…'`` gesendet wird."""
        if isinstance(data, dict):
            data = {
                key: value.decode("ascii") if isinstance(value, bytes) else value
                for key, value in data.items()
            }
        return self.client.post(url, data=data, **kwargs)


def _token_exchange_error(result: dict[str, Any]) -> str:
    """Liefert höchstens einen numerischen Entra-Code, nie fremde Beschreibungstexte."""
    codes = result.get("error_codes")
    if isinstance(codes, list) and codes:
        code = codes[0]
        if isinstance(code, int) and 0 <= code <= 99_999_999:
            return f"auth_{code}"
        if isinstance(code, str) and code.isascii() and code.isdigit() and len(code) <= 8:
            return f"auth_{code}"
    return "auth"


def validate_id_token(
    token: str,
    jwks: dict[str, Any],
    cfg: SsoSettings,
    nonce: str,
) -> str:
    """Prüft ein signiertes ID-Token und gibt ausschließlich die stabile Objekt-ID zurück."""
    try:
        header = jwt.get_unverified_header(token)
        keys = [key for key in jwks["keys"] if key.get("kid") == header.get("kid")]
        if header.get("alg") != "RS256" or not header.get("kid") or len(keys) != 1:
            raise SsoError("token")
        key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=cfg.client_id,
            issuer=cfg.issuer,
            leeway=60,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "nonce", "oid", "tid", "sub"]},
        )
        expected = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        if (
            claims["tid"] != cfg.tenant_id
            or claims["aud"] != cfg.client_id
            or not isinstance(claims["nonce"], str)
            or not hmac.compare_digest(claims["nonce"], expected)
        ):
            raise SsoError("token")
        return str(uuid.UUID(claims["oid"]))
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
        raise SsoError("token") from None


@dataclass
class EntraGrant:
    """Anmeldung einschließlich des nur im Prozess gehaltenen MSAL-Caches."""

    object_id: str
    application: Any


class EntraProvider:
    """Kurzlebiger Client pro Anmeldevorgang/Sitzung; keine Tokenpersistenz."""

    def __init__(self, cfg: SsoSettings, secrets_dir: Path, public_url: str) -> None:
        self.cfg = cfg
        self.redirect_uri = public_url + "/auth/entra/callback"
        self.http = httpx.Client(timeout=10.0, follow_redirects=False)
        try:
            pem = read_secret(cfg.certificate_secret, secrets_dir)
            self.application = msal.ConfidentialClientApplication(
                cfg.client_id,
                authority=f"https://login.microsoftonline.com/{cfg.tenant_id}",
                client_credential={"private_key": pem, "thumbprint": cfg.certificate_thumbprint},
                http_client=_MsalHttpClient(self.http),
            )
        except (ConfigError, ValueError, OSError, httpx.HTTPError):
            self.close()
            raise SsoError("configuration") from None

    def close(self) -> None:
        """Gibt Netzwerkressourcen frei und verwirft den Clientcache mit dem Objekt."""
        self.http.close()

    def begin(self) -> dict[str, Any]:
        """MSAL erzeugt State, Nonce und PKCE; Callback erfolgt ausschließlich per POST."""
        try:
            return cast(
                dict[str, Any],
                self.application.initiate_auth_code_flow(
                    SCOPES,
                    redirect_uri=self.redirect_uri,
                    response_mode="form_post",
                ),
            )
        except (ValueError, OSError, httpx.HTTPError):
            raise SsoError("auth") from None

    def complete(self, flow: dict[str, Any], response: dict[str, str]) -> EntraGrant:
        """Löst den Code ein; prüft State/PKCE/Nonce mit MSAL und das signierte ID-Token selbst."""
        try:
            result = self.application.acquire_token_by_auth_code_flow(flow, response)
        except RuntimeError:
            raise SsoError("token") from None
        except (ValueError, OSError, httpx.HTTPError, KeyError):
            raise SsoError("auth") from None
        token = result.get("id_token")
        if not isinstance(token, str) or not result.get("access_token"):
            raise SsoError(_token_exchange_error(result))
        try:
            jwks_response = self.http.get(
                f"https://login.microsoftonline.com/{self.cfg.tenant_id}/discovery/v2.0/keys"
            )
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
        except (ValueError, OSError, httpx.HTTPError, KeyError):
            raise SsoError("keys") from None
        oid = validate_id_token(token, jwks, self.cfg, flow["nonce"])
        return EntraGrant(oid, self.application)

    def groups(self, grant: EntraGrant) -> set[str]:
        """Fragt aktuelle transitive Mitgliedschaften mit delegiertem User.Read ab (nur GET).

        Fehler, abgelaufene Zustimmung und unvollständiges Paging erlauben keinen Zugriff.
        """
        try:
            accounts = grant.application.get_accounts()
            if len(accounts) != 1:
                raise SsoError("cache")
            result = grant.application.acquire_token_silent(SCOPES, account=accounts[0])
            if not result or not isinstance(result.get("access_token"), str):
                raise SsoError("refresh")
            headers = {"Authorization": f"Bearer {result['access_token']}"}
            link = "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=id"
            groups: set[str] = set()
            for _ in range(100):
                response = self.http.get(link, headers=headers)
                response.raise_for_status()
                data = response.json()
                groups.update(str(uuid.UUID(item["id"])) for item in data["value"])
                next_link = data.get("@odata.nextLink")
                if next_link is None:
                    return groups
                link = _checked_next_link(next_link)
            raise SsoError("groups")
        except (ValueError, TypeError, KeyError, OSError, httpx.HTTPError, GraphError):
            raise SsoError("groups") from None
