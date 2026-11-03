"""Einstellungen, Mandantenkonfiguration (YAML) und Laden von Docker-Secrets.

Priorität der Einstellungen (aufsteigend): Standardwerte im Code, Umgebungsvariablen ``KW_*``.
Mandanten stehen in ``config/tenants.yaml``; dort stehen nur *Namen* von Secrets, nie deren Werte.
Unbekannte Schlüssel brechen den Start ab, damit Tippfehler nicht still ignoriert werden.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

_SECRET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_THUMBPRINT = re.compile(r"^[0-9A-Fa-f]{40}$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
_CA_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

SecretName = Annotated[str, StringConstraints(pattern=_SECRET_NAME.pattern)]


class ConfigError(Exception):
    """Ungültige oder fehlende Konfiguration. Die Meldung enthält nie Geheimnisse."""


@dataclass(frozen=True)
class Settings:
    """Prozessweite Einstellungen aus Umgebungsvariablen.

    Attributes:
        data_dir: Verzeichnis für SQLite-Datei und Laufdaten (``/data`` im Container).
        database_url: SQLAlchemy-URL; Standard ist eine SQLite-Datei unter ``data_dir``.
        tenants_file: Pfad zur Mandanten-YAML.
        secrets_dir: Verzeichnis der Docker-Secrets (``/run/secrets``).
        sku_names_file: Optionale Zuordnung SKU-Teilenummer -> Anzeigename (YAML).
        log_level: Log-Stufe (``INFO`` o. ä.).
    """

    data_dir: Path
    database_url: str
    tenants_file: Path
    secrets_dir: Path
    sku_names_file: Path
    log_level: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Self:
        """Liest die Einstellungen aus ``env`` (Standard: ``os.environ``)."""
        e = os.environ if env is None else env
        data_dir = Path(e.get("KW_DATA_DIR", "/data"))
        return cls(
            data_dir=data_dir,
            database_url=e.get("KW_DATABASE_URL", f"sqlite:///{data_dir / 'kontenwaechter.db'}"),
            tenants_file=Path(e.get("KW_TENANTS_FILE", "/config/tenants.yaml")),
            secrets_dir=Path(e.get("KW_SECRETS_DIR", "/run/secrets")),
            sku_names_file=Path(e.get("KW_SKU_NAMES_FILE", "/config/sku_names.yaml")),
            log_level=e.get("KW_LOG_LEVEL", "INFO").upper(),
        )


def resolve_ca_file(tenants_file: Path, ca_file: str) -> Path:
    """Löst eine ``ca_file``-Angabe relativ zum Ordner der Mandantendatei auf.

    Args:
        tenants_file: Pfad der ``tenants.yaml``; ihr Ordner ist die Wurzel.
        ca_file: Relativer Pfad aus der Konfiguration.

    Raises:
        ConfigError: Pfad verlässt den Ordner (auch über Symlinks) oder ist keine Datei.
    """
    root = tenants_file.parent.resolve()
    path = (root / ca_file).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ConfigError("CA-Datei nicht lesbar oder außerhalb des Konfigurationsordners")
    return path


def read_secret(name: str, secrets_dir: Path) -> str:
    """Liest ein Docker-Secret ``secrets_dir/<name>``.

    Args:
        name: Name des Secrets; nur ``[A-Za-z0-9._-]``, damit kein Pfad ausbrechen kann.
        secrets_dir: Verzeichnis der Secrets.

    Raises:
        ConfigError: Name ungültig oder Secret fehlt. Die Meldung nennt nie den Inhalt.
    """
    if not _SECRET_NAME.fullmatch(name):
        raise ConfigError("Ungültiger Secret-Name")
    path = secrets_dir / name
    try:
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ConfigError(f"Secret '{name}' nicht lesbar") from exc


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EntraConfig(_Strict):
    """Entra-Anbindung eines Mandanten (App-Registrierung, Zertifikats-Login)."""

    tenant_id: str
    client_id: str
    certificate_secret: SecretName
    certificate_thumbprint: str

    @field_validator("tenant_id", "client_id")
    @classmethod
    def _guid(cls, v: str) -> str:
        try:
            return str(uuid.UUID(v))
        except ValueError as exc:
            raise ValueError("keine gültige GUID") from exc

    @field_validator("certificate_thumbprint")
    @classmethod
    def _thumbprint(cls, v: str) -> str:
        if not _THUMBPRINT.fullmatch(v):
            raise ValueError("Fingerabdruck muss 40 Hex-Zeichen (SHA-1) haben")
        return v.upper()


class AdConfig(_Strict):
    """AD-Anbindung eines Mandanten (LDAPS).

    Attributes:
        domain: DNS-Name der Domäne.
        dcs: Feste Liste von DC-Hostnamen; leer = DCs per DNS-SRV finden.
        bind_user: Dienstkonto (sAMAccountName; mit ``@`` oder ``\\`` unverändert verwendet).
        bind_secret: Name des Docker-Secrets mit dem Passwort.
        ca_file: Optionale CA-Datei (PEM), relativ zum Ordner der ``tenants.yaml``. Fehlt sie,
            gilt der Zertifikatsspeicher des Containers.
        accept_weak_dc_keys_until: Befristete, bewusste Ausnahme für DC-Zertifikate mit zu
            kurzem RSA-Schlüssel (unter 2048 Bit): bis einschließlich diesem Datum wird die
            OpenSSL-Sicherheitsstufe 1 statt 2 verwendet. Kette, Hostname und Ablauf werden
            weiter geprüft. Danach gilt wieder die Standardstufe.
    """

    domain: str = Field(pattern=r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
    dcs: list[str] = Field(default_factory=list)
    bind_user: str = Field(min_length=1)
    bind_secret: SecretName
    ca_file: str | None = None
    accept_weak_dc_keys_until: date | None = None

    @field_validator("dcs")
    @classmethod
    def _dc_names(cls, v: list[str]) -> list[str]:
        if any(not _HOSTNAME.fullmatch(h) for h in v):
            raise ValueError("DC-Name enthält ungültige Zeichen")
        return v

    @field_validator("ca_file")
    @classmethod
    def _ca_file(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _CA_FILE.fullmatch(v) or ".." in v.split("/") or "//" in v:
            raise ValueError("ca_file muss ein relativer Pfad ohne '..' sein")
        return v


class Thresholds(_Strict):
    """Schwellwerte in Tagen. Keine festen Zahlen in der Geschäftslogik."""

    inactive_days: int = Field(default=30, ge=1)
    long_inactive_days: int = Field(default=90, ge=1)
    new_account_grace_days: int = Field(default=14, ge=0)

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.long_inactive_days <= self.inactive_days:
            raise ValueError("long_inactive_days muss größer als inactive_days sein")
        return self


class TenantConfig(_Strict):
    """Ein Mandant (Kunde) mit Entra und/oder AD."""

    slug: str
    name: str = Field(min_length=1)
    enabled: bool = True
    entra: EntraConfig | None = None
    ad: AdConfig | None = None
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # Namensmuster je Kontoart (Phase 4), z. B. {"service": ["svc*", "Sync_*"]}.
    account_patterns: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.fullmatch(v):
            raise ValueError("Kürzel nur aus a-z, 0-9 und '-', höchstens 32 Zeichen")
        return v

    @model_validator(mode="after")
    def _has_source(self) -> Self:
        if self.entra is None and self.ad is None:
            raise ValueError("Mandant braucht mindestens 'entra' oder 'ad'")
        return self


class TenantsFile(_Strict):
    """Inhalt von ``tenants.yaml``."""

    tenants: list[TenantConfig]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        slugs = [t.slug for t in self.tenants]
        if len(slugs) != len(set(slugs)):
            raise ValueError("Mandanten-Kürzel müssen eindeutig sein")
        return self


def load_tenants(path: Path) -> list[TenantConfig]:
    """Lädt und validiert die Mandanten-YAML.

    Raises:
        ConfigError: Datei fehlt, ist kein gültiges YAML oder verletzt das Schema.
            Die Meldung nennt Feldpfade, nie Eingabewerte.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(
            f"Mandantendatei '{path}' nicht lesbar "
            "(Vorlage: cp config/tenants.example.yaml config/tenants.yaml)"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Mandantendatei '{path}' ist kein gültiges YAML") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Mandantendatei '{path}': oberste Ebene muss ein Mapping sein")
    try:
        return TenantsFile.model_validate(raw).tenants
    except ValueError as exc:
        # Validierungsfehler ohne Eingabewerte (Werte könnten Kennungen enthalten).
        errors = getattr(exc, "errors", None)
        if errors is None:
            raise ConfigError(f"Mandantendatei '{path}' ungültig") from exc
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors(include_input=False)
        )
        raise ConfigError(f"Mandantendatei '{path}' ungültig: {details}") from exc
