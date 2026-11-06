"""Regeln: letzte Anmeldung, Status, Kontoart, Empfehlung eines zusammengeführten Kontos.

Reine Funktionen ohne Datenbank und ohne Uhr (``now`` kommt von außen). Fristen stammen aus der
Mandantenkonfiguration (``Thresholds``), nie aus Zahlen in der Logik. Siehe ``docs/plan.md``,
Abschnitt „Flag-Logik“.

Fehlen die Entra-Anmeldewerte ganz (``sign_in_known=False``, Warnung
``sign_in_activity_unavailable``), kann ein Konto ohne neue Anmeldung aus dem AD nicht als inaktiv
gelten: Es bekommt den Status ``unknown`` und wird nicht geflaggt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from fnmatch import fnmatchcase

from app.config import Thresholds


class Status(StrEnum):
    """Status in der Prüfreihenfolge aus dem Plan; Anzeigetexte stehen in der Oberfläche."""

    EXCEPTION = "exception"
    NEW = "new"
    DISABLED = "disabled"
    UNKNOWN = "unknown"
    NEVER = "never"
    INACTIVE_LONG = "inactive_long"
    INACTIVE = "inactive"
    ACTIVE = "active"


class AccountType(StrEnum):
    """Kontoart. ``member_onprem`` = Konto nur im AD (im Plan nicht ausdrücklich genannt)."""

    MEMBER_SYNCED = "member_synced"
    MEMBER_CLOUD = "member_cloud"
    MEMBER_ONPREM = "member_onprem"
    GUEST = "guest"
    ADMIN = "admin"
    SERVICE = "service"
    TEST = "test"
    RESOURCE = "resource"


# Vorgabe nur für „Admin“ (Plan: Name enthält adm/admin). Je Mandant in ``account_patterns``
# überschreibbar; eine leere Liste schaltet die Vorgabe ab.
DEFAULT_PATTERNS: Mapping[str, Sequence[str]] = {AccountType.ADMIN: ("*adm*",)}

# Reihenfolge laut Plan, nach Gast.
_PATTERN_ORDER = (AccountType.ADMIN, AccountType.SERVICE, AccountType.TEST, AccountType.RESOURCE)

REC_REVOKE_THEN_DISABLE = "Lizenz entziehen, dann deaktivieren"
REC_REVOKE = "Lizenz entziehen"
REC_DELETE_GUEST = "Gast löschen"
REC_DELETE = "Löschen"
REC_RESOURCE = "Anmeldung blockieren, ggf. in freigegebenes Postfach umwandeln"
REC_ORPHANED = "Verwaist prüfen"
REC_ONLY_ONPREM = "Nur lokal genutzt"
REC_ONLY_CLOUD = "Nur Cloud genutzt"

_UAC_ACCOUNTDISABLE = 0x2


@dataclass(frozen=True)
class Subject:
    """Alles, was die Regeln über ein Konto wissen müssen (aus Entra und/oder AD)."""

    names: tuple[str, ...]  # Namen für die Mustersuche (UPN-Vorderteil, sAMAccountName)
    in_entra: bool
    in_ad: bool
    is_guest: bool = False
    synced: bool = False  # Entra: onPremisesSyncEnabled
    disabled: bool = False  # in Entra oder AD deaktiviert
    created_at: datetime | None = None  # frühester bekannter Zeitpunkt
    entra_last: datetime | None = None  # max(erfolgreich, nicht interaktiv)
    ad_last: datetime | None = None  # max(lastLogon über alle DCs, lastLogonTimestamp)
    licensed: bool = False  # mindestens eine nicht kostenlose Lizenz
    sign_in_known: bool = True  # Entra liefert Anmeldewerte
    ad_expected: bool = False  # für den Mandanten liegen AD-Daten vor
    has_exception: bool = False  # gültige Ausnahme


@dataclass(frozen=True)
class Verdict:
    """Ergebnis der Regeln für ein Konto."""

    last_login: datetime | None
    account_type: AccountType
    status: Status
    flagged: bool
    recommendation: str | None


def ad_disabled(user_account_control: int | None) -> bool:
    """Prüft das Bit ACCOUNTDISABLE; ``None`` (Stammdaten fehlen) gilt als nicht deaktiviert."""
    return bool(user_account_control and user_account_control & _UAC_ACCOUNTDISABLE)


def latest(values: Iterable[datetime | None]) -> datetime | None:
    """Jüngster vorhandener Zeitpunkt, ``None`` wenn es keinen gibt."""
    present = [v for v in values if v is not None]
    return max(present) if present else None


def last_login(subject: Subject) -> datetime | None:
    """Letzte Anmeldung: Maximum aus allen Entra- und AD-Quellen (nie ``lastSignInDateTime``)."""
    return latest((subject.entra_last, subject.ad_last))


def _is_older(moment: datetime | None, now: datetime, days: int) -> bool:
    return moment is not None and now - moment > timedelta(days=days)


def classify_account(subject: Subject, patterns: Mapping[str, Sequence[str]]) -> AccountType:
    """Kontoart: Gast, dann Namensmuster (Admin, Dienst, Test, Ressource), sonst Mitglied."""
    if subject.is_guest:
        return AccountType.GUEST
    names = [n.casefold() for n in subject.names if n]
    for kind in _PATTERN_ORDER:
        configured = patterns.get(kind.value)
        globs = DEFAULT_PATTERNS.get(kind, ()) if configured is None else configured
        if any(fnmatchcase(n, g.casefold()) for n in names for g in globs):
            return kind
    if subject.in_ad and not subject.in_entra:
        return AccountType.MEMBER_ONPREM
    return AccountType.MEMBER_SYNCED if subject.synced else AccountType.MEMBER_CLOUD


def _inactivity(last: datetime | None, now: datetime, t: Thresholds) -> bool:
    """Mehr als ``inactive_days`` her oder nie angemeldet."""
    return last is None or _is_older(last, now, t.inactive_days)


def _status(subject: Subject, last: datetime | None, now: datetime, t: Thresholds) -> Status:
    if subject.has_exception:
        return Status.EXCEPTION
    if (
        last is None
        and subject.created_at is not None
        and not _is_older(subject.created_at, now, t.new_account_grace_days)
    ):
        return Status.NEW
    if subject.disabled:
        return Status.DISABLED
    if subject.in_entra and not subject.sign_in_known and _inactivity(last, now, t):
        return Status.UNKNOWN
    if last is None:
        return Status.NEVER
    if _is_older(last, now, t.long_inactive_days):
        return Status.INACTIVE_LONG
    if _is_older(last, now, t.inactive_days):
        return Status.INACTIVE
    return Status.ACTIVE


def _long_inactive(subject: Subject, last: datetime | None, now: datetime, t: Thresholds) -> bool:
    """Länger als ``long_inactive_days`` ohne Anmeldung; ohne Anmeldung zählt das Alter des Kontos.

    Unabhängig vom Status, damit auch deaktivierte Konten (oft noch lizenziert) erfasst sind.
    """
    if last is not None:
        return _is_older(last, now, t.long_inactive_days)
    return subject.created_at is None or _is_older(subject.created_at, now, t.long_inactive_days)


def _recommend(
    subject: Subject, kind: AccountType, status: Status, flagged: bool, now: datetime, t: Thresholds
) -> str | None:
    if status in (Status.EXCEPTION, Status.NEW, Status.UNKNOWN):
        return None
    long_inactive = _long_inactive(subject, last_login(subject), now, t)
    if kind is AccountType.RESOURCE and flagged:
        return REC_RESOURCE  # Postfächer nicht einfach entlizenzieren
    if kind is AccountType.GUEST and long_inactive and last_login(subject) is None:
        return REC_DELETE_GUEST
    if kind is AccountType.TEST and flagged:
        return REC_DELETE
    if subject.licensed and long_inactive:
        return REC_REVOKE if status is Status.DISABLED else REC_REVOKE_THEN_DISABLE
    if subject.synced and not subject.in_ad and subject.ad_expected:
        return REC_ORPHANED
    if status is Status.ACTIVE and subject.in_entra and subject.in_ad and subject.sign_in_known:
        entra_active = not _inactivity(subject.entra_last, now, t)
        ad_active = not _inactivity(subject.ad_last, now, t)
        if ad_active and not entra_active:
            return REC_ONLY_ONPREM
        if entra_active and not ad_active:
            return REC_ONLY_CLOUD
    return None


def evaluate(
    subject: Subject,
    thresholds: Thresholds,
    patterns: Mapping[str, Sequence[str]],
    now: datetime,
    account_type_override: AccountType | None = None,
) -> Verdict:
    """Wendet alle Regeln an. ``now`` muss timezone-aware sein.

    Eine manuelle Kontoart (``account_type_override``) ersetzt die automatische für die Empfehlung.
    Geflaggt wird, wer mehr als ``inactive_days`` oder nie angemeldet war; ausgenommen sind
    Ausnahmen, neue Konten und Konten mit unbekannten Anmeldewerten. Deaktivierte Konten zählen mit.
    """
    if now.tzinfo is None:
        raise ValueError("now muss timezone-aware sein")
    last = last_login(subject)
    status = _status(subject, last, now, thresholds)
    kind = account_type_override or classify_account(subject, patterns)
    flagged = status not in (Status.EXCEPTION, Status.NEW, Status.UNKNOWN) and _inactivity(
        last, now, thresholds
    )
    recommendation = _recommend(subject, kind, status, flagged, now, thresholds)
    return Verdict(last, kind, status, flagged, recommendation)
