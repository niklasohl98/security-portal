from __future__ import annotations

import pytest
from argon2 import PasswordHasher

from app.auth import passwords


def _fullwidth(text: str) -> str:
    """Dieselben ASCII-Zeichen als Vollbreite-Formen (NFKC macht sie wieder zu ASCII)."""
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


def test_real_default_hasher_meets_the_owasp_minimum() -> None:
    real = PasswordHasher()
    assert real.memory_cost >= 19456  # KiB (OWASP-Mindestwert für Argon2id)
    assert real.time_cost >= 2
    assert real.hash("x").startswith("$argon2id$")


def test_hash_and_verify_roundtrip_with_unique_salts() -> None:
    h1, h2 = (
        passwords.hash_password("Ein langes Passwort!"),
        passwords.hash_password("Ein langes Passwort!"),
    )
    assert h1.startswith("$argon2id$")
    assert h1 != h2  # eigener Salt je Hash
    assert passwords.verify_password(h1, "Ein langes Passwort!") is True
    assert passwords.verify_password(h1, "ein langes passwort!") is False
    assert passwords.verify_password(h1, "") is False


def test_unicode_is_normalized_before_hashing() -> None:
    h = passwords.hash_password(_fullwidth("PasswortZeichen123"))
    assert passwords.verify_password(h, "PasswortZeichen123") is True
    umlaut = passwords.hash_password("Müller-Passwort-1")  # zusammengesetztes ü
    assert passwords.verify_password(umlaut, "Müller-Passwort-1") is True  # u + Trema


def test_broken_hashes_and_overlong_input_are_simply_wrong() -> None:
    assert passwords.verify_password("kein hash", "x") is False
    assert passwords.verify_password("", "x") is False
    h = passwords.hash_password("abc")
    assert passwords.verify_password(h, "a" * (passwords.MAX_LOGIN_LENGTH + 1)) is False


def test_needs_rehash_detects_old_parameters_and_garbage() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("x")
    assert passwords.needs_rehash(weak) is False  # entspricht dem (im Test billigen) Hasher
    assert passwords.needs_rehash("kein hash") is True
    stronger = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1).hash("x")
    assert passwords.needs_rehash(stronger) is True


def test_burn_time_never_raises() -> None:
    passwords.burn_time("irgendwas")
    passwords.burn_time("a" * 5000)


@pytest.mark.parametrize(
    ("new", "confirm", "expected"),
    [
        ("Ausreichend-Lang-1", "Ausreichend-Lang-1", []),
        ("Ausreichend-Lang-1", "Anders-Lang-12345", ["mismatch"]),
        ("kurz", "kurz", ["too_short"]),
        ("Passwort123", "Passwort123", ["too_short", "forbidden"]),
        ("x" * 129, "x" * 129, ["too_long"]),
        ("meinadmin-passwort", "meinadmin-passwort", ["contains_username"]),
        ("MEINADMIN-passwort", "MEINADMIN-passwort", ["contains_username"]),
    ],
)
def test_password_rules(new: str, confirm: str, expected: list[str]) -> None:
    got = passwords.check_new_password(new, confirm, username="Admin", forbidden=["Passwort123"])
    assert got == expected


def test_forbidden_comparison_ignores_case_and_form() -> None:
    wide = _fullwidth("PASSWORT123")
    got = passwords.check_new_password(wide, wide, username="x", forbidden=["Passwort123"])
    assert "forbidden" in got


def test_exactly_the_minimum_length_is_accepted() -> None:
    ok = "a" * passwords.MIN_LENGTH
    assert passwords.check_new_password(ok, ok, username="Admin") == []
    short = "a" * (passwords.MIN_LENGTH - 1)
    assert passwords.check_new_password(short, short, username="Admin") == ["too_short"]
