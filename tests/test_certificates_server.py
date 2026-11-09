from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.core.certificates import certificate_status, load_inventory
from app.main import create_app
from app.server import main, trusted_proxy_ips
from tests.identity_fakes import NOW


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert(key: rsa.RSAPrivateKey, start: datetime, end: datetime) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-certificate")])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )


def test_public_certificate_inventory_distinguishes_validity_states(
    tmp_path: Path, key: rsa.RSAPrivateKey
) -> None:
    entries = []
    cases = [
        ("valid", -1, 60, "ok"),
        ("soon", -1, 10, "expiring"),
        ("expired", -2, -1, "expired"),
        ("future", 1, 60, "not_yet_valid"),
    ]
    for label, start, end, _ in cases:
        (tmp_path / f"{label}.pem").write_bytes(
            _cert(key, NOW + timedelta(days=start), NOW + timedelta(days=end))
        )
        entries.append({"label": label, "file": f"{label}.pem"})
    entries.append({"label": "missing", "file": "missing.pem"})
    inventory = tmp_path / "certificates.yaml"
    inventory.write_text(yaml.safe_dump({"certificates": entries}))
    assert [status.state for status in certificate_status(inventory, NOW)] == [
        case[-1] for case in cases
    ] + ["unreadable"]
    assert certificate_status(inventory, NOW)[1].days_left == 10
    with pytest.raises(ValueError, match="timezone"):
        certificate_status(inventory, datetime(2026, 11, 6))  # noqa: DTZ001 - bewusst ungültig


@pytest.mark.parametrize(
    "entry",
    [
        {"label": "x", "file": "../private.pem"},
        {"label": "x", "file": "/etc/passwd"},
        {"label": "x", "file": "C:\\private.pem"},
        {"label": "x", "file": "a", "warn_days": 0},
        {"label": "x", "file": "a", "extra": True},
    ],
)
def test_inventory_rejects_invalid_entries_without_values(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    inventory = tmp_path / "certificates.yaml"
    inventory.write_text(yaml.safe_dump({"certificates": [entry]}))
    with pytest.raises(ConfigError, match="Zertifikatsinventar"):
        load_inventory(inventory)
    assert certificate_status(inventory, NOW)[0].state == "unreadable"


def test_inventory_rejects_duplicate_names_invalid_data_and_private_keys(
    tmp_path: Path, key: rsa.RSAPrivateKey
) -> None:
    inventory = tmp_path / "certificates.yaml"
    assert load_inventory(inventory) == []
    inventory.write_text("certificates: [{label: x, file: x}, {label: x, file: x}]")
    with pytest.raises(ConfigError):
        load_inventory(inventory)
    inventory.write_text("certificates: [broken")
    with pytest.raises(ConfigError):
        load_inventory(inventory)
    inventory.write_text("certificates: [{label: private, file: private.pem}]")
    (tmp_path / "private.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    assert certificate_status(inventory, NOW)[0].state == "unreadable"


def test_certificate_warning_is_visible_in_the_authenticated_ui(
    settings: Settings,
    frozen_clock: object,
    tmp_path: Path,
    key: rsa.RSAPrivateKey,
) -> None:
    inventory = tmp_path / "certificates.yaml"
    inventory.write_text("certificates: [{label: Entra-Mail-SSO, file: cert.pem, warn_days: 30}]")
    (tmp_path / "cert.pem").write_bytes(
        _cert(key, NOW - timedelta(days=1), NOW + timedelta(days=5))
    )
    with TestClient(create_app(replace(settings, certificates_file=inventory))) as client:
        client.post("/login", data={"username": "Admin", "password": "Passwort123"})
        # Initialkonto darf nur Passwortwechsel sehen; vor der Ansicht erst Passwort setzen.
        from app.auth import service
        from app.auth.context import COOKIE_NAME
        from tests.web_fakes import open_session

        with open_session(client) as session:
            info = service.load_session(session, client.cookies[COOKIE_NAME], NOW)
            assert info is not None
        response = client.post(
            "/password",
            data={
                "current_password": "Passwort123",
                "new_password": "Ein-sicheres-neues-Passwort-7",
                "confirm_password": "Ein-sicheres-neues-Passwort-7",
                "csrf_token": info.csrf_token,
            },
        )
        assert response.status_code == 200
        assert "Zertifikat Entra-Mail-SSO" in response.text
        assert "läuft bald ab" in client.get("/runs").text


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "::/0", "", "127.0.0.1,*", "host.example"])
def test_universal_or_ambiguous_proxy_trust_is_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match="KW_TRUSTED_PROXY_IPS"):
        trusted_proxy_ips(value)


def test_server_logs_json_before_uvicorn_startup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert trusted_proxy_ips("127.0.0.1, 192.0.2.3/32, ::1") == "127.0.0.1,192.0.2.3/32,::1"
    observed: dict[str, Any] = {}

    def run(app: str, **kwargs: Any) -> None:
        observed.update(kwargs)
        logging.getLogger("uvicorn.error").info("Started server process")

    monkeypatch.setattr("app.server.uvicorn.run", run)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    main()
    assert json.loads(capsys.readouterr().out)["msg"] == "Started server process"
    assert observed["log_config"] is None
    assert observed["access_log"] is False
    assert observed["workers"] == 1
