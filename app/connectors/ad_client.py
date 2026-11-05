"""LDAPS-Verbindung zu einem Domänencontroller (ldap3). Nur lesend, immer mit Zertifikatsprüfung.

Sicherheitsregeln dieses Moduls:

* Port 636 mit TLS ab dem ersten Byte, nie LDAP ohne TLS, nie STARTTLS-Rückfall.
* ``CERT_REQUIRED``: Kette gegen die CA-Datei des Mandanten (sonst Zertifikatsspeicher des
  Containers) und Hostname gegen den DC-Namen. Es gibt keinen Schalter, der die Prüfung abschaltet.
* Schreibschutz doppelt: ``read_only=True`` sperrt in ldap3 nur ``modify``, ``delete`` und
  ``modify_dn``; ``add`` und ``extended`` (z. B. Passwortänderung) sperrt ``_ReadOnlyConnection``
  zusätzlich. ``LdapsDirectory`` bietet nach außen ohnehin nur Lesen an.
* Fehler werden auf feste Codes abgebildet (``AdError``); Meldungen von ldap3 enthalten
  Kennungen und werden nicht weitergereicht.

Einzige Aufweichung: ``TlsPolicy.allow_weak_keys`` senkt für diese eine Verbindung die
OpenSSL-Sicherheitsstufe von 2 auf 1, damit DC-Zertifikate mit RSA-1024-Schlüssel akzeptiert
werden (befristete, bewusste Ausnahme, siehe ADR 0005). Kette und Hostname bleiben geprüft.
"""

from __future__ import annotations

import contextlib
import socket
import ssl
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ldap3 import BASE, NONE, SIMPLE, SUBTREE, SYNC, Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPCertificateError,
    LDAPConnectionIsReadOnlyError,
    LDAPException,
    LDAPInvalidCredentialsResult,
    LDAPSSLConfigurationError,
)

from app.connectors.ad_parse import RawAttrs, normalize

LDAPS_PORT = 636
PAGE_SIZE = 1000
# Sicherheitsstufe wird immer ausdrücklich gesetzt, damit sie nicht von der Python-Installation
# abhängt: Stufe 2 verlangt RSA >= 2048 Bit, Stufe 1 erlaubt RSA >= 1024 Bit.
_STRICT_CIPHERS = "DEFAULT:@SECLEVEL=2"
_WEAK_KEY_CIPHERS = "DEFAULT:@SECLEVEL=1"

ERR_TLS = "tls"
ERR_CONNECT = "connect"
ERR_BIND = "bind"
ERR_LDAP = "ldap"
ERR_NO_HEALTHY_DC = "no_healthy_dc"
ERR_NO_DC = "no_dc_found"


class AdError(Exception):
    """Fehler bei der AD-Abfrage. ``code`` ist fest; die Meldung enthält nie Inhalte."""

    def __init__(self, code: str) -> None:
        super().__init__(f"AdError code={code}")
        self.code = code


@dataclass(frozen=True)
class TlsPolicy:
    """TLS-Einstellungen für die Verbindung zu den DCs eines Mandanten."""

    ca_file: Path | None = None
    allow_weak_keys: bool = False


@dataclass(frozen=True)
class Entry:
    """Ein Verzeichniseintrag: DN und Attribute (Namen klein, Werte als Bytes)."""

    dn: str
    attrs: RawAttrs


class Directory(Protocol):
    """Schnittstelle zu einem DC; in Tests durch Attrappen ersetzt."""

    def root_naming_context(self) -> str:
        """``defaultNamingContext`` aus der RootDSE (z. B. ``DC=example,DC=invalid``)."""
        ...

    def search(self, base: str, search_filter: str, attributes: Sequence[str]) -> Iterator[Entry]:
        """Durchsucht den Teilbaum seitenweise und liefert die Einträge."""
        ...

    def close(self) -> None:
        """Beendet die Verbindung."""
        ...


Opener = Callable[[str], Directory]


def classify(exc: BaseException) -> str:
    """Bildet eine ldap3-/Socket-Ausnahme auf einen festen Fehlercode ab."""
    if isinstance(exc, ssl.SSLError | LDAPCertificateError | LDAPSSLConfigurationError):
        return ERR_TLS
    if isinstance(exc, LDAPBindError | LDAPInvalidCredentialsResult):
        return ERR_BIND
    if isinstance(exc, OSError | socket.timeout) or type(exc).__name__.startswith("LDAPSocket"):
        return ERR_CONNECT
    return ERR_LDAP


class _ReadOnlyConnection(Connection):  # type: ignore[misc]
    """ldap3-Verbindung, die jede schreibende Operation verweigert."""

    def add(self, *_args: Any, **_kwargs: Any) -> None:
        raise LDAPConnectionIsReadOnlyError("connection is read-only")

    def extended(self, *_args: Any, **_kwargs: Any) -> None:
        raise LDAPConnectionIsReadOnlyError("connection is read-only")


def new_connection(
    server: Any,
    user: str,
    password: str,
    receive_timeout: int,
    client_strategy: str = SYNC,
) -> Any:
    """Bindet eine schreibgeschützte Verbindung (``client_strategy`` nur für Tests anders)."""
    return _ReadOnlyConnection(
        server,
        user=user,
        password=password,
        authentication=SIMPLE,
        auto_bind=True,
        raise_exceptions=True,
        read_only=True,
        check_names=False,
        receive_timeout=receive_timeout,
        client_strategy=client_strategy,
    )


class LdapsDirectory:
    """Gebundene, schreibgeschützte LDAPS-Verbindung zu einem DC."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def root_naming_context(self) -> str:
        """``defaultNamingContext`` der RootDSE."""
        try:
            self._conn.search(
                "", "(objectClass=*)", search_scope=BASE, attributes=["defaultNamingContext"]
            )
            for item in self._conn.response or []:
                if item.get("type") == "searchResEntry":
                    values = normalize(item["raw_attributes"]).get("defaultnamingcontext") or []
                    if values:
                        return str(values[0].decode("utf-8"))
        except LDAPException as exc:
            raise AdError(classify(exc)) from None
        raise AdError(ERR_LDAP)

    def search(self, base: str, search_filter: str, attributes: Sequence[str]) -> Iterator[Entry]:
        """Seitenweise Suche (Seitengröße 1000) über den Teilbaum."""
        try:
            for item in self._conn.extend.standard.paged_search(
                base,
                search_filter,
                search_scope=SUBTREE,
                attributes=list(attributes),
                paged_size=PAGE_SIZE,
                generator=True,
            ):
                if item.get("type") == "searchResEntry":
                    yield Entry(str(item["dn"]), normalize(item["raw_attributes"]))
        except LDAPException as exc:
            raise AdError(classify(exc)) from None

    def close(self) -> None:
        """Beendet die Verbindung; Fehler beim Schließen sind unerheblich."""
        with contextlib.suppress(LDAPException, OSError):
            self._conn.unbind()


def bind_name(bind_user: str, domain: str) -> str:
    """Anmeldename für den Bind: ``name`` wird zu ``name@domäne``; ``@``/``\\`` bleiben."""
    if "@" in bind_user or "\\" in bind_user:
        return bind_user
    return f"{bind_user}@{domain}"


def open_ldaps(
    host: str,
    *,
    bind_user: str,
    password: str,
    policy: TlsPolicy,
    connect_timeout: float = 5.0,
    receive_timeout: int = 60,
    port: int = LDAPS_PORT,
) -> LdapsDirectory:
    """Öffnet LDAPS zu ``host`` und bindet das Dienstkonto.

    Args:
        host: DC-Hostname (FQDN); gegen ihn wird das Zertifikat geprüft.
        bind_user: Anmeldename (siehe ``bind_name``).
        password: Passwort des Dienstkontos (nie loggen).
        policy: CA-Datei und ggf. die befristete Schlüssel-Ausnahme.
        connect_timeout: Sekunden für den Verbindungsaufbau.
        receive_timeout: Ganze Sekunden je Antwort (inkl. TLS-Handshake); ldap3 verlangt
            einen int, ein Float bricht mit ``struct.error`` ab.
        port: LDAPS-Port.

    Raises:
        AdError: ``tls``, ``connect``, ``bind`` oder ``ldap``; ohne Inhalte aus ldap3.
    """
    try:
        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=str(policy.ca_file) if policy.ca_file else None,
            ciphers=_WEAK_KEY_CIPHERS if policy.allow_weak_keys else _STRICT_CIPHERS,
        )
        server = Server(
            host, port=port, use_ssl=True, tls=tls, get_info=NONE, connect_timeout=connect_timeout
        )
        conn = new_connection(server, bind_user, password, receive_timeout)
    except (LDAPException, OSError) as exc:
        raise AdError(classify(exc)) from None
    return LdapsDirectory(conn)
