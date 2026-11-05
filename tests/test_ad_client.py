"""Der ldap3-Wrapper: Schreibschutz, Rohwerte, Fehlerabbildung (ldap3-Mock-Backend)."""

from __future__ import annotations

import ssl
import uuid
from typing import Any

import pytest
from ldap3 import MOCK_SYNC, Server
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPCertificateError,
    LDAPConnectionIsReadOnlyError,
    LDAPException,
    LDAPSocketOpenError,
)

from app.connectors.ad import USER_FILTER, read_last_logons, read_master_data
from app.connectors.ad_client import (
    ERR_BIND,
    ERR_CONNECT,
    ERR_LDAP,
    ERR_TLS,
    AdError,
    LdapsDirectory,
    bind_name,
    classify,
    new_connection,
)

BASE = "dc=beispiel,dc=invalid"
SVC = f"cn=svc,{BASE}"


@pytest.fixture
def conn() -> Any:
    """Gebundene Mock-Verbindung mit fünf Testbenutzern, aufgebaut wie in ``open_ldaps``."""
    c = new_connection(Server("fake"), SVC, "pw", 5, client_strategy=MOCK_SYNC)
    c.unbind()
    c.strategy.add_entry(SVC, {"userPassword": "pw", "objectClass": ["person"]})
    for n in range(5):
        c.strategy.add_entry(
            f"cn=u{n},ou=t,{BASE}",
            {
                "objectClass": ["top", "person", "user"],
                "objectCategory": "person",
                "sAMAccountName": f"u{n}",
                "objectGUID": uuid.UUID(int=n).bytes_le,
                "userAccountControl": 512,
            },
        )
    c.bind()
    return c


def test_search_returns_raw_lowercase_attributes(conn: Any) -> None:
    entries = list(LdapsDirectory(conn).search(BASE, USER_FILTER, ["objectGUID", "sAMAccountName"]))
    assert len(entries) == 5
    first = entries[0]
    assert set(first.attrs) == {"objectguid", "samaccountname"}
    assert all(isinstance(v, bytes) for vals in first.attrs.values() for v in vals)


def test_connector_reads_through_the_wrapper(conn: Any) -> None:
    users = read_master_data(LdapsDirectory(conn), BASE)
    assert len(users) == 5
    assert {u.sam_account_name for u in users.values()} == {f"u{n}" for n in range(5)}
    assert all(u.user_account_control == 512 for u in users.values())
    assert len(read_last_logons(LdapsDirectory(conn), BASE)) == 5


def test_rodc_krbtgt_accounts_are_not_read(conn: Any) -> None:
    """``krbtgt_<Zahl>`` (RODC-Schlüsselkonten) sind keine Benutzer; ``krbtgt`` selbst bleibt."""
    for n, sam in enumerate(("krbtgt_30674", "krbtgt"), start=100):
        conn.strategy.add_entry(
            f"cn={sam},cn=Users,{BASE}",
            {
                "objectClass": ["top", "person", "user"],
                "objectCategory": "person",
                "sAMAccountName": sam,
                "objectGUID": uuid.UUID(int=n).bytes_le,
                "userAccountControl": 514,
            },
        )
    users = read_master_data(LdapsDirectory(conn), BASE)
    names = {u.sam_account_name for u in users.values()}
    assert "krbtgt_30674" not in names
    assert {"krbtgt", *(f"u{n}" for n in range(5))} == names
    assert len(read_last_logons(LdapsDirectory(conn), BASE)) == 6


@pytest.mark.parametrize(
    "operation",
    [
        lambda c: c.add(f"cn=neu,{BASE}", "person", {}),
        lambda c: c.modify(SVC, {"description": [("MODIFY_REPLACE", ["x"])]}),
        lambda c: c.delete(SVC),
        lambda c: c.modify_dn(SVC, "cn=svc2"),
        lambda c: c.extended("1.3.6.1.4.1.4203.1.11.1"),
    ],
    ids=["add", "modify", "delete", "modify_dn", "extended"],
)
def test_connection_refuses_every_write_operation(conn: Any, operation: Any) -> None:
    with pytest.raises(LDAPConnectionIsReadOnlyError):
        operation(conn)
    assert conn.search(BASE, "(sAMAccountName=u1)")  # Lesen geht weiter


def test_directory_exposes_no_write_methods(conn: Any) -> None:
    public = {n for n in dir(LdapsDirectory(conn)) if not n.startswith("_")}
    assert public == {"root_naming_context", "search", "close"}


def test_search_errors_become_fixed_codes(conn: Any) -> None:
    class Broken:
        extend = None

        def search(self, *_a: object, **_k: object) -> None:
            raise LDAPException("enthält Name=Max Muster")

    directory = LdapsDirectory(Broken())
    with pytest.raises(AdError) as caught:
        directory.root_naming_context()
    assert caught.value.code == ERR_LDAP
    assert "Max" not in str(caught.value)
    assert caught.value.__cause__ is None  # keine Meldung von ldap3 weiterreichen


def test_root_naming_context_reads_rootdse() -> None:
    class Fake:
        def __init__(self, response: list[dict[str, Any]]) -> None:
            self.response = response

        def search(self, *_a: object, **_k: object) -> bool:
            return True

    entry = {"type": "searchResEntry", "raw_attributes": {"defaultNamingContext": [b"DC=a,DC=b"]}}
    assert LdapsDirectory(Fake([entry])).root_naming_context() == "DC=a,DC=b"

    with pytest.raises(AdError) as caught:
        LdapsDirectory(Fake([])).root_naming_context()
    assert caught.value.code == ERR_LDAP


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ssl.SSLCertVerificationError("x"), ERR_TLS),
        (LDAPCertificateError("x"), ERR_TLS),
        (LDAPBindError("x"), ERR_BIND),
        (ConnectionRefusedError("x"), ERR_CONNECT),
        (TimeoutError("x"), ERR_CONNECT),
        (LDAPSocketOpenError("x"), ERR_CONNECT),
        (LDAPException("x"), ERR_LDAP),
        (ValueError("x"), ERR_LDAP),
    ],
)
def test_classify(exc: BaseException, code: str) -> None:
    assert classify(exc) == code


def test_ad_error_message_is_only_the_code() -> None:
    assert str(AdError("tls")) == "AdError code=tls"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("svc", "svc@beispiel.invalid"),
        ("svc@andere.invalid", "svc@andere.invalid"),
        ("BEISPIEL\\svc", "BEISPIEL\\svc"),
    ],
)
def test_bind_name(given: str, expected: str) -> None:
    assert bind_name(given, "beispiel.invalid") == expected


def test_search_failure_becomes_a_fixed_code() -> None:
    class Standard:
        def paged_search(self, *_a: object, **_k: object) -> None:
            raise LDAPException("enthält Name=Max Muster")

    class Conn:
        extend = type("Ext", (), {"standard": Standard()})()

    with pytest.raises(AdError) as caught:
        list(LdapsDirectory(Conn()).search(BASE, USER_FILTER, ["objectGUID"]))
    assert (caught.value.code, caught.value.__cause__) == (ERR_LDAP, None)


def test_close_ignores_errors() -> None:
    class Conn:
        def unbind(self) -> None:
            raise LDAPException("schon getrennt")

    LdapsDirectory(Conn()).close()  # darf nicht werfen
