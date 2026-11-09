"""Explizit aktivierte Entra-Anmeldung; alle geheimen Werte bleiben Docker-Secrets."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import SECRET_NAME_PATTERN, THUMBPRINT_PATTERN, ConfigError, positive_int


@dataclass(frozen=True)
class SsoSettings:
    """Eigene SSO-Einstellungen, auch beim Wiederverwenden der Mail-App-Registrierung."""

    tenant_id: str
    client_id: str
    allowed_group_id: str
    certificate_secret: str
    certificate_thumbprint: str
    admin_group_id: str | None = None
    recheck_seconds: int = 300

    @property
    def issuer(self) -> str:
        """Exakter Aussteller des einzelnen freigegebenen Tenants."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SsoSettings | None:
        """Liest KW_SSO_*; ohne ausdrückliches Aktivieren wird nichts eingerichtet."""
        enabled = env.get("KW_SSO_ENABLED", "0").strip().lower()
        if enabled in ("", "0", "false"):
            return None
        if enabled not in ("1", "true"):
            raise ConfigError("KW_SSO_ENABLED muss 0 oder 1 sein")

        def guid(key: str) -> str:
            try:
                return str(uuid.UUID(env.get(key, "").strip()))
            except ValueError:
                raise ConfigError(f"{key} muss eine GUID sein") from None

        secret = env.get("KW_SSO_CERT_SECRET", "").strip()
        thumbprint = env.get("KW_SSO_CERT_THUMBPRINT", "").strip()
        if not SECRET_NAME_PATTERN.fullmatch(secret):
            raise ConfigError("KW_SSO_CERT_SECRET fehlt oder ist ungültig")
        if not THUMBPRINT_PATTERN.fullmatch(thumbprint):
            raise ConfigError("KW_SSO_CERT_THUMBPRINT muss 40 Hex-Zeichen haben")
        seconds = positive_int(env, "KW_SSO_RECHECK_SECONDS", "300")
        if seconds > 900:
            raise ConfigError("KW_SSO_RECHECK_SECONDS darf höchstens 900 sein")
        return cls(
            tenant_id=guid("KW_SSO_TENANT_ID"),
            client_id=guid("KW_SSO_CLIENT_ID"),
            allowed_group_id=guid("KW_SSO_ALLOWED_GROUP_ID"),
            certificate_secret=secret,
            certificate_thumbprint=thumbprint.upper(),
            admin_group_id=guid("KW_SSO_ADMIN_GROUP_ID")
            if env.get("KW_SSO_ADMIN_GROUP_ID")
            else None,
            recheck_seconds=seconds,
        )
