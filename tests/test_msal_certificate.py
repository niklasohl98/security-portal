"""Prüft den Zertifikats-Login mit echter MSAL-Bibliothek, echtem Schlüssel und Fake-HTTP.

Es geht kein Netzverkehr raus: der HTTP-Client von MSAL ist ersetzt. Geprüft wird, dass das
Client-Assertion-JWT mit dem privaten Schlüssel signiert ist und den Fingerabdruck trägt.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import msal
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.connectors.graph import MsalCertificateTokenProvider

TENANT = "00000000-0000-4000-8000-0000000000aa"


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None


class _FakeHttp:
    """Beantwortet OpenID-Konfiguration und Token-Endpunkt; merkt sich den Token-Aufruf."""

    def __init__(self) -> None:
        self.token_request: dict[str, Any] | None = None

    def get(self, url: str, **_kw: Any) -> _Resp:
        base = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
        return _Resp(
            {
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            }
        )

    def post(self, url: str, data: dict[str, Any] | None = None, **_kw: Any) -> _Resp:
        self.token_request = {"url": url, "data": data or {}}
        return _Resp(
            {"access_token": "fake-access-token", "token_type": "Bearer", "expires_in": 3600}
        )

    def close(self) -> None:
        return None


def test_client_assertion_is_signed_with_private_key_and_carries_thumbprint() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kontenwaechter-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=730))
        .sign(key, hashes.SHA256())
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    sha1 = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER), usedforsecurity=False)
    thumbprint = sha1.hexdigest().upper()

    http = _FakeHttp()

    def factory(client_id: str, **kwargs: Any) -> msal.ConfidentialClientApplication:
        return msal.ConfidentialClientApplication(
            client_id, http_client=http, instance_discovery=False, **kwargs
        )

    provider = MsalCertificateTokenProvider(
        TENANT, "00000000-0000-4000-8000-0000000000bb", pem, thumbprint, app_factory=factory
    )
    assert provider.get_token() == "fake-access-token"

    assert http.token_request is not None
    data = http.token_request["data"]
    assert data["grant_type"] == "client_credentials"
    assert data["scope"] == "https://graph.microsoft.com/.default"
    assert "client_secret" not in data
    assertion = data["client_assertion"]

    header = jwt.get_unverified_header(assertion)
    assert header["alg"] == "RS256"
    # MSAL hängt Base64-Padding an; entscheidend ist der dekodierte SHA-1-Fingerabdruck.
    padded = header["x5t"] + "=" * (-len(header["x5t"]) % 4)
    assert base64.urlsafe_b64decode(padded) == bytes.fromhex(thumbprint)
    claims = jwt.decode(
        assertion,
        key.public_key(),
        algorithms=["RS256"],
        audience=http.token_request["url"],
    )
    assert claims["iss"] == claims["sub"] == "00000000-0000-4000-8000-0000000000bb"
