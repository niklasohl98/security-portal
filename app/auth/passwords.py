"""Passwörter: Argon2id-Hash, Prüfung und Regeln für neue Passwörter.

Hash und Prüfung nutzen ``argon2-cffi`` mit den Voreinstellungen der Bibliothek (Argon2id, RFC 9106,
Profil mit wenig Speicher). Eingaben werden vorher nach Unicode NFKC normalisiert, damit gleich
aussehende Eingaben denselben Hash ergeben. Regeln (NIST-nah, ohne Zeichenklassen-Zwang): 12 bis
128 Zeichen, nicht der Benutzername darin, nicht ein bekanntes Standardpasswort.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128
# Obergrenze für Anmeldeeingaben: längere sind nie gültig und sollen keine Rechenzeit kosten.
MAX_LOGIN_LENGTH = 1024

_hasher = PasswordHasher()
_dummy_hash: str | None = None


def _normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    """Argon2id-Hash (mit Salt und Parametern im Ergebnis)."""
    return _hasher.hash(_normalize(password))


def verify_password(stored_hash: str, password: str) -> bool:
    """Prüft ein Passwort gegen den Hash; jeder Fehler (auch ein defekter Hash) heißt ``False``."""
    if len(password) > MAX_LOGIN_LENGTH:
        return False
    try:
        return _hasher.verify(stored_hash, _normalize(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Ob der Hash mit veralteten Parametern erzeugt wurde (dann beim Login neu berechnen)."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def burn_time(password: str) -> None:
    """Rechnet gegen einen Scheinhash, damit unbekannte Benutzer nicht schneller antworten."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("kein-echtes-passwort-nur-fuer-die-laufzeit")
    verify_password(_dummy_hash, password)


def check_new_password(
    new: str, confirm: str, *, username: str, forbidden: Iterable[str] = ()
) -> list[str]:
    """Regelverstöße eines neuen Passworts als feste Codes (leer = in Ordnung).

    Codes: ``mismatch``, ``too_short``, ``too_long``, ``contains_username``, ``forbidden``.
    """
    errors: list[str] = []
    if new != confirm:
        errors.append("mismatch")
    if len(new) < MIN_LENGTH:
        errors.append("too_short")
    if len(new) > MAX_LENGTH:
        errors.append("too_long")
    folded = _normalize(new).casefold()
    if username and _normalize(username).casefold() in folded:
        errors.append("contains_username")
    if any(_normalize(f).casefold() == folded for f in forbidden):
        errors.append("forbidden")
    return errors
