"""Echte TLS-Handshakes gegen einen lokalen Testserver.

Beweist die Sicherheitszusagen von ``open_ldaps``: Kette, Hostname und Ablauf werden immer
geprüft; die befristete Ausnahme erlaubt nur kürzere RSA-Schlüssel und sonst nichts. Der
Testserver spricht TLS und beantwortet ein LDAP-Bind mit einem festen Ergebnis (Erfolg oder
„ungültige Anmeldedaten“); mehr kann er nicht. Zertifikate werden zur Laufzeit erzeugt, es
liegen keine Schlüssel im Repository.
"""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.connectors.ad_client import (
    ERR_BIND,
    ERR_TLS,
    AdError,
    LdapsDirectory,
    TlsPolicy,
    open_ldaps,
)

HOST = "dc1.test.invalid"


@dataclass(frozen=True)
class Pki:
    """Test-CA und ein Serverzertifikat (PEM-Dateien)."""

    ca: Path
    cert: Path
    key: Path


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def make_ca(tmp: Path, label: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate, Path]:
    """Erzeugt eine Test-CA (RSA 2048) und schreibt ihr Zertifikat."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(label))
        .issuer_name(_name(label))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    path = _write(tmp / f"{label}.pem", cert.public_bytes(serialization.Encoding.PEM))
    return key, cert, path


def make_server_cert(
    tmp: Path,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    ca_path: Path,
    *,
    name: str,
    bits: int,
    expired: bool = False,
) -> Pki:
    """Serverzertifikat für ``name`` mit RSA-Schlüssel der Länge ``bits``."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    now = datetime.now(UTC)
    start, end = (
        (now - timedelta(days=3), now - timedelta(days=1))
        if expired
        else (now - timedelta(days=1), now + timedelta(days=1))
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    tag = f"{name}-{bits}-{'abgelaufen' if expired else 'gueltig'}"
    return Pki(
        ca=ca_path,
        cert=_write(tmp / f"{tag}.crt", cert.public_bytes(serialization.Encoding.PEM)),
        key=_write(
            tmp / f"{tag}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
    )


def _bind_response(request: bytes, result_code: int) -> bytes:
    """BER-kodierte BindResponse mit der Nachrichten-ID der Anfrage (leere DN und Diagnose)."""
    offset = 2 + (request[1] & 0x7F if request[1] & 0x80 else 0)  # nach dem SEQUENCE-Kopf
    message_id = request[offset : offset + 2 + request[offset + 1]]  # INTEGER samt Tag und Länge
    body = bytes([0x61, 0x07, 0x0A, 0x01, result_code, 0x04, 0x00, 0x04, 0x00])
    return bytes([0x30, len(message_id) + len(body)]) + message_id + body


class TlsServer:
    """Nimmt TLS-Verbindungen an und beantwortet das erste LDAP-Bind mit ``bind_result``."""

    def __init__(self, cert: Path, key: Path, bind_result: int = 0) -> None:
        self._bind_result = bind_result
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Der Testserver muss auch das 1024-Bit-Zertifikat laden dürfen.
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        ctx.load_cert_chain(cert, key)
        self._ctx = ctx
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                with self._ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.settimeout(2)
                    request = tls.recv(4096)  # BindRequest
                    if request:
                        tls.sendall(_bind_response(request, self._bind_result))
                        tls.recv(4096)  # UnbindRequest oder Ende der Verbindung
            except (ssl.SSLError, OSError):
                conn.close()

    def close(self) -> None:
        self._sock.close()


@pytest.fixture(autouse=True)
def _resolve_test_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testnamen (``*.test.invalid``) lokal auflösen, alles andere bleibt unverändert."""
    real = socket.getaddrinfo

    def fake(host: str | bytes | None, *args: object, **kwargs: object) -> object:
        if isinstance(host, str) and host.endswith(".test.invalid"):
            host = "127.0.0.1"
        return real(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def ca(tmp_path: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate, Path]:
    return make_ca(tmp_path, "Test-CA")


@pytest.fixture
def other_ca(tmp_path: Path) -> Path:
    return make_ca(tmp_path, "Andere-CA")[2]


@pytest.fixture
def serve() -> Iterator[list[TlsServer]]:
    servers: list[TlsServer] = []
    yield servers
    for s in servers:
        s.close()


def _attempt(
    serve: list[TlsServer],
    pki: Pki,
    policy: TlsPolicy,
    host: str = HOST,
    bind_result: int = 0,
) -> str | None:
    """Verbindet; liefert den ``AdError``-Code oder ``None``, wenn Handshake und Bind gelangen."""
    server = TlsServer(pki.cert, pki.key, bind_result)
    serve.append(server)
    try:
        open_ldaps(
            host,
            bind_user="svc@test.invalid",
            password="x",
            policy=policy,
            port=server.port,
            connect_timeout=3,
            receive_timeout=3,
        )
    except AdError as exc:
        return exc.code
    return None


def _tls_accepted(code: str | None) -> bool:
    """Handshake und Bind gelangen (kein Fehler)."""
    return code is None


def test_valid_certificate_is_accepted(tmp_path: Path, ca: tuple, serve: list[TlsServer]) -> None:  # type: ignore[type-arg]
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    assert _tls_accepted(_attempt(serve, pki, TlsPolicy(ca_file=pki.ca)))


def test_wrong_hostname_is_rejected(tmp_path: Path, ca: tuple, serve: list[TlsServer]) -> None:  # type: ignore[type-arg]
    pki = make_server_cert(tmp_path, *ca, name="anderer.test.invalid", bits=2048)
    assert _attempt(serve, pki, TlsPolicy(ca_file=pki.ca)) == ERR_TLS


def test_unknown_ca_is_rejected(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    other_ca: Path,
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    assert _attempt(serve, pki, TlsPolicy(ca_file=other_ca)) == ERR_TLS


def test_private_ca_is_rejected_without_ca_file(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    """Ohne ``ca_file`` gilt der Zertifikatsspeicher des Systems, nicht „alles erlaubt“."""
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    assert _attempt(serve, pki, TlsPolicy()) == ERR_TLS


def test_expired_certificate_is_rejected(tmp_path: Path, ca: tuple, serve: list[TlsServer]) -> None:  # type: ignore[type-arg]
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048, expired=True)
    assert _attempt(serve, pki, TlsPolicy(ca_file=pki.ca)) == ERR_TLS


def test_weak_key_is_rejected_by_default(tmp_path: Path, ca: tuple, serve: list[TlsServer]) -> None:  # type: ignore[type-arg]
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=1024)
    assert _attempt(serve, pki, TlsPolicy(ca_file=pki.ca)) == ERR_TLS


def test_weak_key_is_accepted_only_with_exception(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=1024)
    assert _tls_accepted(_attempt(serve, pki, TlsPolicy(ca_file=pki.ca, allow_weak_keys=True)))


def test_exception_does_not_relax_hostname_check(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name="anderer.test.invalid", bits=1024)
    policy = TlsPolicy(ca_file=pki.ca, allow_weak_keys=True)
    assert _attempt(serve, pki, policy) == ERR_TLS


def test_exception_does_not_relax_chain_check(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    other_ca: Path,
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=1024)
    assert _attempt(serve, pki, TlsPolicy(ca_file=other_ca, allow_weak_keys=True)) == ERR_TLS


def test_exception_does_not_relax_expiry_check(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=1024, expired=True)
    assert _attempt(serve, pki, TlsPolicy(ca_file=pki.ca, allow_weak_keys=True)) == ERR_TLS


def test_missing_ca_file_is_a_tls_error(tmp_path: Path) -> None:
    with pytest.raises(AdError) as caught:
        open_ldaps(
            HOST, bind_user="svc", password="x", policy=TlsPolicy(ca_file=tmp_path / "fehlt.pem")
        )
    assert caught.value.code == ERR_TLS


def test_connection_refused_is_a_connect_error() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # Port ist frei, nichts lauscht
    with pytest.raises(AdError) as caught:
        open_ldaps(
            HOST, bind_user="svc", password="x", policy=TlsPolicy(), port=port, connect_timeout=2
        )
    assert caught.value.code == "connect"


def test_successful_connection_returns_a_working_directory(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    server = TlsServer(pki.cert, pki.key)
    serve.append(server)
    directory = open_ldaps(
        HOST,
        bind_user="svc@test.invalid",
        password="x",
        policy=TlsPolicy(ca_file=pki.ca),
        port=server.port,
        connect_timeout=3,
        receive_timeout=3,
    )
    assert isinstance(directory, LdapsDirectory)
    directory.close()
    directory.close()  # zweites Schließen darf nicht werfen


def test_default_receive_timeout_works_with_ldap3(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    """Regression: ldap3 packt den Timeout per ``struct.pack('LL', ...)`` und braucht einen int."""
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    server = TlsServer(pki.cert, pki.key)
    serve.append(server)
    directory = open_ldaps(
        HOST,
        bind_user="svc@test.invalid",
        password="x",
        policy=TlsPolicy(ca_file=pki.ca),
        port=server.port,
    )
    directory.close()


def test_rejected_credentials_are_a_bind_error(
    tmp_path: Path,
    ca: tuple,  # type: ignore[type-arg]
    serve: list[TlsServer],
) -> None:
    pki = make_server_cert(tmp_path, *ca, name=HOST, bits=2048)
    assert _attempt(serve, pki, TlsPolicy(ca_file=pki.ca), bind_result=49) == ERR_BIND
