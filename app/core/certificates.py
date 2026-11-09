"""Ablaufüberwachung ausdrücklich hinterlegter öffentlicher Zertifikate, ohne private Schlüssel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

import yaml
from cryptography import x509
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import ConfigError, resolve_ca_file
from app.db.models import utcnow


class CertificateEntry(BaseModel):
    """Ein technischer Anzeigename, öffentlicher Dateiverweis und konfigurierbare Vorwarnfrist."""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    file: str = Field(min_length=1, max_length=128)
    warn_days: int = Field(default=30, ge=1, le=365)

    @field_validator("file")
    @classmethod
    def relative_file(cls, value: str) -> str:
        """Verhindert absolute Pfade, Traversal und abweichende Plattform-Pfadseparatoren."""
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
            raise ValueError("relativer Zertifikatspfad erforderlich")
        return value


class _Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    certificates: list[CertificateEntry] = Field(max_length=100)


@dataclass(frozen=True)
class CertificateStatus:
    """Ablaufzustand ohne Subject/UPN/privates Schlüsselmaterial."""

    label: str
    state: str
    expires_at: datetime | None = None
    days_left: int | None = None


def load_inventory(path: Path) -> list[CertificateEntry]:
    """Fehlendes optionales Inventar ergibt keine Einträge; ungültiges Inventar einen Fehler."""
    if not path.exists():
        return []
    try:
        entries = _Inventory.model_validate(yaml.safe_load(path.read_text())).certificates
        if len({entry.label for entry in entries}) != len(entries):
            raise ValueError
        return entries
    except (OSError, yaml.YAMLError, ValidationError, ValueError):
        raise ConfigError("Zertifikatsinventar ungültig oder nicht lesbar") from None


def read_public_certificate(path: Path) -> x509.Certificate:
    """Liest nur öffentliche PEM-/DER-Zertifikate; private PEM-Bestandteile werden abgelehnt."""
    data = path.read_bytes()
    if b"PRIVATE KEY" in data:
        raise ValueError("öffentliches Zertifikat erforderlich")
    return (
        x509.load_pem_x509_certificate(data)
        if b"-----BEGIN CERTIFICATE-----" in data
        else x509.load_der_x509_certificate(data)
    )


def certificate_status(path: Path, now: datetime | None = None) -> list[CertificateStatus]:
    """Zeigt Ablauf, noch nicht gültige und fehlende Dateien. Dies ist keine TLS-Kettenprüfung."""
    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError("now muss timezone-aware sein")
    try:
        entries = load_inventory(path)
    except ConfigError:
        return [CertificateStatus("Inventar", "unreadable")]
    statuses = []
    for entry in entries:
        try:
            cert = read_public_certificate(resolve_ca_file(path, entry.file))
        except (OSError, ValueError, ConfigError):
            statuses.append(CertificateStatus(entry.label, "unreadable"))
            continue
        delta = cert.not_valid_after_utc - now
        if cert.not_valid_before_utc > now:
            state = "not_yet_valid"
        elif delta <= timedelta(0):
            state = "expired"
        elif delta <= timedelta(days=entry.warn_days):
            state = "expiring"
        else:
            state = "ok"
        statuses.append(CertificateStatus(entry.label, state, cert.not_valid_after_utc, delta.days))
    return statuses
