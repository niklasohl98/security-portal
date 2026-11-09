"""Zeitlich und mengenmäßig begrenzter Prozessspeicher für SSO-Flows und Token-Caches.

Nach einem Neustart müssen SSO-Benutzer erneut anmelden. Lokale Notzugänge benötigen diesen
Speicher und eine Verbindung zu Entra nicht. Es werden keine Tokens in Datenbank/Dateien abgelegt.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.auth.sso_provider import EntraGrant, EntraProvider, SsoError
from app.auth.sso_settings import SsoSettings
from app.db.models import utcnow

FLOW_SECONDS = 600
MAX_FLOWS = 128
MAX_SESSIONS = 1024


@dataclass
class PendingFlow:
    """Ein einmalig konsumierbarer Anmeldevorgang mit serverseitigem PKCE/State/Nonce."""

    provider: EntraProvider
    flow: dict[str, Any]
    target: str
    expires: datetime


@dataclass
class ActiveGrant:
    """Eine Sitzung mit erneuerbarer Zulassung und eigener Sperre gegen parallelen Refresh."""

    provider: EntraProvider
    grant: EntraGrant
    is_admin: bool
    checked: datetime
    expires: datetime
    lock: Any = field(default_factory=threading.Lock)


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SsoRuntime:
    """Prozesslokaler SSO-Speicher; Zugriffssperren umfassen keine Datenbanktransaktionen."""

    def __init__(self, cfg: SsoSettings, secrets_dir: Path, public_url: str) -> None:
        self.cfg, self.secrets_dir, self.public_url = cfg, secrets_dir, public_url
        self.flows: dict[str, PendingFlow] = {}
        self.sessions: dict[str, ActiveGrant] = {}
        self.lock = threading.RLock()

    def _prune(self) -> None:
        now = utcnow()
        for key, pending in list(self.flows.items()):
            if pending.expires <= now:
                pending.provider.close()
                del self.flows[key]
        for key, active in list(self.sessions.items()):
            if active.expires <= now:
                active.provider.close()
                del self.sessions[key]

    def begin(self, target: str) -> tuple[str, str]:
        """Beginnt einen Login und liefert Browserbindung und vertrauenswürdige Auth-URL."""
        with self.lock:
            self._prune()
            if len(self.flows) >= MAX_FLOWS:
                raise SsoError("busy")
        provider = EntraProvider(self.cfg, self.secrets_dir, self.public_url)
        try:
            flow = provider.begin()
            uri = flow["auth_uri"]
            if not isinstance(uri, str) or not uri.startswith(
                f"https://login.microsoftonline.com/{self.cfg.tenant_id}/"
            ):
                raise SsoError("auth")
            token = secrets.token_urlsafe(32)
            with self.lock:
                if len(self.flows) >= MAX_FLOWS:
                    raise SsoError("busy")
                self.flows[_key(token)] = PendingFlow(
                    provider,
                    flow,
                    target,
                    utcnow() + timedelta(seconds=FLOW_SECONDS),
                )
            return token, uri
        except (SsoError, KeyError):
            provider.close()
            raise SsoError("auth") from None

    def consume(self, token: str) -> PendingFlow:
        """Entnimmt einen noch gültigen Flow genau einmal, gebunden an das Browsercookie."""
        with self.lock:
            self._prune()
            pending = self.flows.pop(_key(token), None)
        if pending is None:
            raise SsoError("state")
        return pending

    def allowed(self, provider: EntraProvider, grant: EntraGrant) -> bool:
        """Prüft die Zugangsgruppe; Rückgabe ist die unabhängig bestimmte Admin-Rolle."""
        groups = provider.groups(grant)
        if self.cfg.allowed_group_id not in groups:
            raise SsoError("group")
        return self.cfg.admin_group_id is not None and self.cfg.admin_group_id in groups

    def register(
        self,
        token: str,
        provider: EntraProvider,
        grant: EntraGrant,
        is_admin: bool,
        expires: datetime,
    ) -> None:
        """Übernimmt nach erfolgreichem Login den Cache; wird beim Logout wieder verworfen."""
        with self.lock:
            self._prune()
            if len(self.sessions) >= MAX_SESSIONS:
                raise SsoError("busy")
            self.sessions[_key(token)] = ActiveGrant(provider, grant, is_admin, utcnow(), expires)

    def access(self, token: str) -> bool:
        """Prüft laufende Sitzungen regelmäßig neu. Jeder Fehler entzieht den Zugang."""
        with self.lock:
            self._prune()
            active = self.sessions.get(_key(token))
        if active is None:
            raise SsoError("session")
        with active.lock:
            if utcnow() - active.checked >= timedelta(seconds=self.cfg.recheck_seconds):
                active.is_admin = self.allowed(active.provider, active.grant)
                active.checked = utcnow()
            return active.is_admin

    def forget(self, token: str) -> None:
        """Verwirft genau einen Sitzungscache ohne externe Abmeldung bei Entra."""
        with self.lock:
            active = self.sessions.pop(_key(token), None)
        if active is not None:
            active.provider.close()

    def close(self) -> None:
        """Verwirft beim Herunterfahren alle Flow- und Sitzungscaches."""
        with self.lock:
            for pending in self.flows.values():
                pending.provider.close()
            for active in self.sessions.values():
                active.provider.close()
            self.flows.clear()
            self.sessions.clear()
