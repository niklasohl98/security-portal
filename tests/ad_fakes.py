"""Attrappen für den AD-Abruf. Alle Daten sind erfunden; keine echten Mandantendaten."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

from app.connectors.ad import USER_FILTER
from app.connectors.ad_client import AdError, Directory, Entry, Opener

BASE_DN = "DC=beispiel,DC=invalid"
SERVICE_ACCOUNT = "svc-kontenwaechter"
_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def guid(n: int) -> str:
    """Erfundene GUID aus einer laufenden Nummer."""
    return str(uuid.UUID(int=n))


def filetime(moment: datetime) -> int:
    """Windows-FILETIME zu einem Zeitpunkt."""
    return int((moment - _EPOCH).total_seconds()) * 10_000_000


def _b(value: object) -> list[bytes]:
    return [str(value).encode()]


def user(
    n: int,
    *,
    uac: int | None = 512,
    created: str | None = "20240101120000.0Z",
    logon_ts: datetime | None = None,
    last_logon: datetime | None = None,
    member_of: Sequence[str] = (),
) -> Entry:
    """Ein Benutzer-Eintrag; ``last_logon`` wird nur in der ``lastLogon``-Sicht benutzt."""
    attrs: dict[str, list[bytes]] = {
        "objectguid": [uuid.UUID(int=n).bytes_le],
        "samaccountname": _b(f"user{n:03d}"),
        "userprincipalname": _b(f"user{n:03d}@beispiel.invalid"),
        "mail": _b(f"user{n:03d}@beispiel.invalid"),
        "pwdlastset": _b(filetime(datetime(2024, 1, 2, tzinfo=UTC))),
        "accountexpires": _b(0x7FFFFFFFFFFFFFFF),
        "memberof": [m.encode() for m in member_of],
    }
    if uac is not None:
        attrs["useraccountcontrol"] = _b(uac)
    if created is not None:
        attrs["whencreated"] = _b(created)
    if logon_ts is not None:
        attrs["lastlogontimestamp"] = _b(filetime(logon_ts))
    if last_logon is not None:
        attrs["lastlogon"] = _b(filetime(last_logon))
    return Entry(f"CN=Nutzer {n}\\, Beispiel,OU=Test,{BASE_DN}", attrs)


def ref(sam: str, *, filled: bool = True) -> Entry:
    """Referenzobjekt (Dienstkonto, krbtgt, Administrator); ``filled=False`` = leere Attribute."""
    attrs: dict[str, list[bytes]] = {"samaccountname": _b(sam)}
    if filled:
        attrs["useraccountcontrol"] = _b(512)
        attrs["whencreated"] = _b("20200101000000.0Z")
    return Entry(f"CN={sam},CN=Users,{BASE_DN}", attrs)


class FakeDirectory:
    """Ein DC mit festen Antworten. ``logons`` bildet die nicht replizierte ``lastLogon``-Sicht."""

    def __init__(
        self,
        users: Sequence[Entry],
        logons: dict[int, datetime] | None = None,
        refs: dict[str, bool | None] | None = None,
    ) -> None:
        self.users = list(users)
        self.logons = logons or {}
        # None = Objekt fehlt, False = vorhanden aber leer, True = gefüllt
        self.refs = refs if refs is not None else {SERVICE_ACCOUNT: True, "krbtgt": True}
        self.closed = False
        self.searches: list[str] = []

    def root_naming_context(self) -> str:
        return BASE_DN

    def search(self, base: str, search_filter: str, attributes: Sequence[str]) -> Iterator[Entry]:
        self.searches.append(search_filter)
        assert base == BASE_DN
        if search_filter == USER_FILTER:
            if "lastLogon" in attributes:
                for u in self.users:
                    n = uuid.UUID(bytes_le=u.attrs["objectguid"][0]).int
                    attrs = {"objectguid": u.attrs["objectguid"]}
                    if n in self.logons:
                        attrs["lastlogon"] = _b(filetime(self.logons[n]))
                    yield Entry(u.dn, attrs)
            else:
                yield from self.users
            return
        for sam, state in self.refs.items():
            if search_filter == f"(sAMAccountName={sam})" and state is not None:
                yield ref(sam, filled=state)

    def close(self) -> None:
        self.closed = True


def opener_for(dcs: dict[str, Directory | AdError]) -> Opener:
    """Öffner, der je Hostname eine Attrappe liefert oder den hinterlegten Fehler wirft."""

    def open_(host: str) -> Directory:
        result = dcs[host]
        if isinstance(result, AdError):
            raise result
        return result

    return open_
