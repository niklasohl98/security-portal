"""AD-Abruf eines Mandanten: DCs finden, Gesundheitscheck, Konten und ``lastLogon`` aller DCs.

Ablauf (siehe ``docs/plan.md``, Abschnitt AD-Anbindung):

1. DCs finden: feste Liste aus der Konfiguration, sonst DNS-SRV ``_ldap._tcp.dc._msdcs.<domäne>``.
2. Je DC verbinden und prüfen: drei Referenzobjekte (Dienstkonto, ``krbtgt``, ``Administrator``)
   müssen gefüllte ``userAccountControl`` und ``whenCreated`` liefern, sonst gilt der DC als defekt
   und liefert keine Stammdaten.
3. Stammdaten (Paged Search) vom ersten gesunden DC; Konten mit leeren Stammdaten werden von einem
   zweiten gesunden DC ergänzt und markiert.
4. Von jedem erreichbaren DC ``objectGUID`` und ``lastLogon``; je Konto zählt der jüngste Wert.
5. Objektanzahl je DC gegen den Median: Abweichung über 1 % macht den DC „verdächtig“.

Nur lesend. Die Fehlertexte enthalten nur feste Codes, keine Namen oder Werte.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import dns.exception
import dns.resolver
from ldap3.utils.conv import escape_filter_chars

from app.connectors.ad_client import (
    ERR_NO_DC,
    ERR_NO_HEALTHY_DC,
    AdError,
    Directory,
    Opener,
)
from app.connectors.ad_parse import (
    RawAttrs,
    all_str,
    first_filetime,
    first_int,
    first_str,
    generalized_time,
    guid_to_str,
    parent_dn,
)

log = logging.getLogger(__name__)

USER_FILTER = "(&(objectCategory=person)(objectClass=user))"
COUNT_DEVIATION = 0.01  # Abweichung der Objektanzahl vom Median, ab der ein DC verdächtig ist
MIN_DCS_FOR_COUNT_CHECK = 3
SRV_TIMEOUT = 5.0

WARN_DC_UNREACHABLE = "ad_dc_unreachable"
WARN_DC_DEFECTIVE = "ad_dc_defective"
WARN_DC_SUSPECT = "ad_dc_suspect"
WARN_MASTER_INCOMPLETE = "ad_master_data_incomplete"

REASON_REFERENCE_MISSING = "reference_missing"
REASON_REFERENCE_EMPTY = "reference_empty"
REASON_COUNT_DEVIATES = "count_deviates"

_REFERENCE_ATTRS = ["sAMAccountName", "userAccountControl", "whenCreated"]
_MASTER_ATTRS = [
    "objectGUID",
    "sAMAccountName",
    "userPrincipalName",
    "mail",
    "userAccountControl",
    "whenCreated",
    "pwdLastSet",
    "lastLogonTimestamp",
    "accountExpires",
    "description",
    "memberOf",
]


class DcStatus(StrEnum):
    """Zustand eines DCs nach der Prüfung."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    DEFECTIVE = "defective"
    SUSPECT = "suspect"


@dataclass(frozen=True)
class DcReport:
    """Ergebnis der Prüfung eines DCs (nur technische Angaben)."""

    host: str
    status: DcStatus
    reason: str | None = None
    user_count: int | None = None


@dataclass
class AdAccountData:
    """Ein AD-Konto mit Stammdaten und ``lastLogon`` aller DCs."""

    object_guid: str
    sam_account_name: str | None = None
    user_principal_name: str | None = None
    mail: str | None = None
    user_account_control: int | None = None
    when_created: datetime | None = None
    pwd_last_set: datetime | None = None
    last_logon: datetime | None = None
    last_logon_per_dc: dict[str, str | None] = field(default_factory=dict)
    last_logon_timestamp: datetime | None = None
    account_expires: datetime | None = None
    description: str | None = None
    distinguished_name: str | None = None
    ou: str | None = None
    member_of: list[str] = field(default_factory=list)
    master_data_incomplete: bool = False


@dataclass
class AdCollection:
    """Ergebnis eines AD-Abrufs."""

    accounts: list[AdAccountData]
    dcs: list[DcReport]
    warnings: list[str]
    master_host: str


class NoHealthyDcError(AdError):
    """Kein DC war erreichbar und gesund. ``reports`` hält die Einzelergebnisse."""

    def __init__(self, reports: list[DcReport]) -> None:
        super().__init__(ERR_NO_HEALTHY_DC)
        self.reports = reports


def resolve_srv(domain: str) -> list[str]:
    """Sucht die DCs per DNS-SRV ``_ldap._tcp.dc._msdcs.<domäne>``.

    Raises:
        AdError: ``no_dc_found``, wenn die Abfrage scheitert oder nichts liefert.
    """
    try:
        answer = dns.resolver.Resolver().resolve(
            f"_ldap._tcp.dc._msdcs.{domain}", "SRV", lifetime=SRV_TIMEOUT
        )
        return sorted({str(r.target).rstrip(".").lower() for r in answer})
    except dns.exception.DNSException:
        raise AdError(ERR_NO_DC) from None


def discover_dcs(
    domain: str,
    configured: list[str],
    resolver: Callable[[str], list[str]] = resolve_srv,
) -> list[str]:
    """DC-Hostnamen: feste Liste aus der Konfiguration, sonst DNS-SRV (sortiert, ohne Doppelte)."""
    hosts = sorted({h.lower() for h in configured}) if configured else resolver(domain)
    if not hosts:
        raise AdError(ERR_NO_DC)
    return hosts


def _is_filled(attrs: RawAttrs) -> bool:
    created = first_str(attrs, "whenCreated")
    return first_int(attrs, "userAccountControl") is not None and (
        created is not None and generalized_time(created) is not None
    )


def check_reference_objects(directory: Directory, base_dn: str, service_account: str) -> str | None:
    """Prüft Dienstkonto, ``krbtgt`` und ``Administrator``; ``None`` heißt gesund, sonst Grund.

    ``Administrator`` darf fehlen (kann umbenannt sein); die anderen müssen existieren.
    """
    for sam, required in ((service_account, True), ("krbtgt", True), ("Administrator", False)):
        flt = f"(sAMAccountName={escape_filter_chars(sam)})"
        entries = list(directory.search(base_dn, flt, _REFERENCE_ATTRS))
        if not entries:
            if required:
                return REASON_REFERENCE_MISSING
            continue
        if not _is_filled(entries[0].attrs):
            return REASON_REFERENCE_EMPTY
    return None


def read_master_data(directory: Directory, base_dn: str) -> dict[str, AdAccountData]:
    """Liest alle Benutzer mit Stammdaten; Schlüssel ist die ``objectGUID``."""
    users: dict[str, AdAccountData] = {}
    for entry in directory.search(base_dn, USER_FILTER, _MASTER_ATTRS):
        a = entry.attrs
        raw_guid = (a.get("objectguid") or [b""])[0]
        guid = guid_to_str(raw_guid)
        if guid is None:
            continue
        created = first_str(a, "whenCreated")
        uac = first_int(a, "userAccountControl")
        users[guid] = AdAccountData(
            object_guid=guid,
            sam_account_name=first_str(a, "sAMAccountName"),
            user_principal_name=first_str(a, "userPrincipalName"),
            mail=first_str(a, "mail"),
            user_account_control=uac,
            when_created=generalized_time(created) if created else None,
            pwd_last_set=first_filetime(a, "pwdLastSet"),
            last_logon_timestamp=first_filetime(a, "lastLogonTimestamp"),
            account_expires=first_filetime(a, "accountExpires"),
            description=first_str(a, "description"),
            distinguished_name=entry.dn,
            ou=parent_dn(entry.dn),
            member_of=all_str(a, "memberOf"),
            master_data_incomplete=uac is None or created is None,
        )
    return users


def read_last_logons(directory: Directory, base_dn: str) -> dict[str, datetime | None]:
    """Liest ``objectGUID`` und ``lastLogon`` aller Benutzer eines DCs (nicht repliziert)."""
    result: dict[str, datetime | None] = {}
    for entry in directory.search(base_dn, USER_FILTER, ["objectGUID", "lastLogon"]):
        guid = guid_to_str((entry.attrs.get("objectguid") or [b""])[0])
        if guid is not None:
            result[guid] = first_filetime(entry.attrs, "lastLogon")
    return result


def _fill_from(target: dict[str, AdAccountData], other: dict[str, AdAccountData]) -> None:
    """Ergänzt leere Stammdaten markierter Konten aus den Daten eines zweiten DCs."""
    for guid, account in target.items():
        src = other.get(guid)
        if not account.master_data_incomplete or src is None:
            continue
        for name in (
            "sam_account_name",
            "user_principal_name",
            "mail",
            "user_account_control",
            "when_created",
            "pwd_last_set",
            "last_logon_timestamp",
            "account_expires",
            "description",
        ):
            if getattr(account, name) is None:
                setattr(account, name, getattr(src, name))


@dataclass
class _State:
    """Zwischenstand über alle DCs."""

    users: dict[str, AdAccountData] | None = None
    master_host: str = ""
    filled: bool = False
    logons: dict[str, dict[str, datetime | None]] = field(default_factory=dict)
    reports: dict[str, DcReport] = field(default_factory=dict)


def _visit_dc(host: str, opener: Opener, service_account: str, state: _State) -> None:
    """Verbindet zu einem DC, prüft ihn und liest je nach Zustand Stammdaten und ``lastLogon``."""
    try:
        directory = opener(host)
    except AdError as exc:
        state.reports[host] = DcReport(host, DcStatus.UNREACHABLE, exc.code)
        return
    try:
        base_dn = directory.root_naming_context()
        reason = check_reference_objects(directory, base_dn, service_account)
        state.logons[host] = read_last_logons(directory, base_dn)
        count = len(state.logons[host])
        if reason is not None:
            state.reports[host] = DcReport(host, DcStatus.DEFECTIVE, reason, count)
            return
        state.reports[host] = DcReport(host, DcStatus.OK, None, count)
        if state.users is None:
            state.users = read_master_data(directory, base_dn)
            state.master_host = host
        elif not state.filled and any(u.master_data_incomplete for u in state.users.values()):
            _fill_from(state.users, read_master_data(directory, base_dn))
            state.filled = True  # nur einmal nachlesen, die Markierung bleibt bestehen
    except AdError as exc:
        state.reports[host] = DcReport(host, DcStatus.UNREACHABLE, exc.code)
    finally:
        directory.close()


def _flag_count_outliers(state: _State) -> None:
    """Markiert DCs, deren Objektanzahl um mehr als 1 % vom Median abweicht, als verdächtig."""
    ok = [r for r in state.reports.values() if r.status is DcStatus.OK and r.user_count]
    if len(ok) < MIN_DCS_FOR_COUNT_CHECK:
        return
    median = statistics.median(r.user_count for r in ok if r.user_count is not None)
    for r in ok:
        if r.user_count is not None and abs(r.user_count - median) / median > COUNT_DEVIATION:
            state.reports[r.host] = DcReport(
                r.host, DcStatus.SUSPECT, REASON_COUNT_DEVIATES, r.user_count
            )


def _merge_logons(state: _State) -> None:
    """Trägt je Konto den jüngsten ``lastLogon`` aller DCs und die Einzelwerte ein."""
    assert state.users is not None  # noqa: S101 - vom Aufrufer geprüft
    for guid, account in state.users.items():
        seen = {h: logons[guid] for h, logons in state.logons.items() if guid in logons}
        account.last_logon_per_dc = {h: v.isoformat() if v else None for h, v in seen.items()}
        values = [v for v in seen.values() if v is not None]
        account.last_logon = max(values) if values else None


def _warnings(reports: list[DcReport], accounts: list[AdAccountData]) -> list[str]:
    warnings: list[str] = []
    for status, code in (
        (DcStatus.UNREACHABLE, WARN_DC_UNREACHABLE),
        (DcStatus.DEFECTIVE, WARN_DC_DEFECTIVE),
        (DcStatus.SUSPECT, WARN_DC_SUSPECT),
    ):
        if any(r.status is status for r in reports):
            warnings.append(code)
    if any(a.master_data_incomplete for a in accounts):
        warnings.append(WARN_MASTER_INCOMPLETE)
    return warnings


def collect_ad(hosts: list[str], opener: Opener, service_account: str) -> AdCollection:
    """Führt den AD-Abruf über alle DCs aus.

    Args:
        hosts: DC-Hostnamen (aus ``discover_dcs``).
        opener: Öffnet eine gebundene Verbindung zu einem DC (``AdError`` bei Fehlern).
        service_account: Kurzname des Dienstkontos (Referenzobjekt für den DC-Check).

    Raises:
        NoHealthyDcError: Kein DC war erreichbar und gesund.
    """
    state = _State()
    for host in hosts:
        _visit_dc(host, opener, service_account, state)
        report = state.reports[host]
        log.info(
            "AD-DC geprüft: dc=%s status=%s grund=%s konten=%s",
            host,
            report.status.value,
            report.reason,
            report.user_count,
        )
    if state.users is None:
        raise NoHealthyDcError(list(state.reports.values()))
    _flag_count_outliers(state)
    _merge_logons(state)
    reports = [state.reports[h] for h in hosts]
    accounts = sorted(state.users.values(), key=lambda a: a.object_guid)
    return AdCollection(accounts, reports, _warnings(reports, accounts), state.master_host)
