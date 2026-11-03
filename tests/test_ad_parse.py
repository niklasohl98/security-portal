from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.connectors.ad_parse import (
    all_str,
    filetime_to_datetime,
    first_filetime,
    first_int,
    first_str,
    generalized_time,
    guid_to_str,
    normalize,
    parent_dn,
    sam_from_bind_user,
)


@pytest.mark.parametrize("raw", [0, -1, 0x7FFFFFFFFFFFFFFF, 2**64])
def test_filetime_never_values_are_none(raw: int) -> None:
    assert filetime_to_datetime(raw) is None


def test_filetime_converts_to_utc() -> None:
    # Bekannter Referenzwert: 1970-01-01 00:00:00 UTC (Unix-Nullpunkt) in FILETIME.
    assert filetime_to_datetime(116444736000000000) == datetime(1970, 1, 1, tzinfo=UTC)
    # Eine Sekunde später; der Bruchteil unter einer Mikrosekunde wird abgeschnitten.
    assert filetime_to_datetime(116444736010000009) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


def test_generalized_time() -> None:
    assert generalized_time("20240131120000.0Z") == datetime(2024, 1, 31, 12, 0, tzinfo=UTC)
    assert generalized_time("kein Datum") is None


def test_guid_uses_windows_byte_order() -> None:
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert guid_to_str(value.bytes_le) == str(value)
    assert guid_to_str(b"kurz") is None


def test_first_helpers_handle_missing_empty_and_invalid() -> None:
    attrs = normalize({"sAMAccountName": [b"abc"], "uac": [b"x"], "leer": [b""], "n": [b"512"]})
    assert first_str(attrs, "samaccountname") == "abc"
    assert first_str(attrs, "SAMACCOUNTNAME") == "abc"  # Groß-/Kleinschreibung egal
    assert first_str(attrs, "leer") is None
    assert first_str(attrs, "fehlt") is None
    assert first_int(attrs, "n") == 512
    assert first_int(attrs, "uac") is None
    assert first_int(attrs, "fehlt") is None
    assert first_filetime(attrs, "fehlt") is None
    assert all_str(normalize({"memberOf": [b"a", b"b"]}), "memberOf") == ["a", "b"]


def test_parent_dn_keeps_escaped_commas() -> None:
    assert parent_dn("CN=Muster\\, Max,OU=Test,DC=x") == "OU=Test,DC=x"
    assert parent_dn("DC=x") is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [("svc", "svc"), ("svc@beispiel.invalid", "svc"), ("BEISPIEL\\svc", "svc")],
)
def test_sam_from_bind_user(given: str, expected: str) -> None:
    assert sam_from_bind_user(given) == expected
