"""Umwandlung roher LDAP-Werte aus dem AD (Bytes) in Python-Typen.

Alle Zeiten sind UTC-aware. ``None`` bedeutet „nie“ oder „nicht vorhanden“.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

# Windows-FILETIME zählt 100-ns-Schritte seit 1601-01-01 UTC.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_FILETIME_NEVER = 0x7FFFFFFFFFFFFFFF

RawAttrs = Mapping[str, Sequence[bytes]]  # Schlüssel in Kleinbuchstaben (siehe ``normalize``)

_PARENT_DN = re.compile(r"^(?:\\.|[^,\\])*,(.*)$")


def normalize(attrs: Mapping[str, Sequence[bytes]]) -> dict[str, list[bytes]]:
    """Attributnamen in Kleinbuchstaben: LDAP ist hier nicht groß-/kleinschreibungsabhängig."""
    return {k.lower(): list(v) for k, v in attrs.items()}


def filetime_to_datetime(raw: int) -> datetime | None:
    """Wandelt FILETIME in ``datetime``; 0 und der Höchstwert bedeuten „nie“ (``None``)."""
    if raw <= 0 or raw >= _FILETIME_NEVER:
        return None
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=raw // 10)
    except OverflowError:
        return None


def generalized_time(value: str) -> datetime | None:
    """Wandelt LDAP-GeneralizedTime (``20240131120000.0Z``) in UTC; ``None`` bei ungültigem Wert."""
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def guid_to_str(raw: bytes) -> str | None:
    """Wandelt ``objectGUID`` (16 Byte, Windows-Reihenfolge) in die übliche GUID-Schreibweise."""
    if len(raw) != 16:
        return None
    return str(uuid.UUID(bytes_le=raw))


def first_str(attrs: RawAttrs, name: str) -> str | None:
    """Erster Wert als Text; leere und fehlende Attribute ergeben ``None``."""
    values = attrs.get(name.lower()) or []
    if not values:
        return None
    text = values[0].decode("utf-8", errors="replace")
    return text or None


def first_int(attrs: RawAttrs, name: str) -> int | None:
    """Erster Wert als Ganzzahl; nicht numerische Werte ergeben ``None``."""
    text = first_str(attrs, name)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def first_filetime(attrs: RawAttrs, name: str) -> datetime | None:
    """Erster Wert eines FILETIME-Attributs als ``datetime`` (``None`` = nie/leer)."""
    raw = first_int(attrs, name)
    return None if raw is None else filetime_to_datetime(raw)


def all_str(attrs: RawAttrs, name: str) -> list[str]:
    """Alle Werte eines mehrwertigen Attributs als Texte."""
    return [v.decode("utf-8", errors="replace") for v in attrs.get(name.lower()) or []]


def parent_dn(dn: str) -> str | None:
    """Übergeordneter Container eines DN (die OU des Kontos); maskierte Kommas bleiben heil."""
    match = _PARENT_DN.match(dn)
    return match.group(1) if match else None


def sam_from_bind_user(bind_user: str) -> str:
    """Kurzname (sAMAccountName) aus ``name``, ``name@domäne`` oder ``DOMÄNE\\name``."""
    if "\\" in bind_user:
        return bind_user.rsplit("\\", 1)[1]
    return bind_user.split("@", 1)[0]
