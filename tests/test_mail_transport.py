"""SMTP-Versand mit einem Attrappen-Server: Verschlüsselung, Anmeldung, feste Fehlercodes."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.mail.settings import MailSettings
from app.mail.transport import MailError, build_message, check_connection, send_message

SECRET_VALUE = "geheim-passwort-123"


class FakeSMTP:
    """Ersatz für ``smtplib.SMTP``/``SMTP_SSL``; merkt sich Aufrufe, wirft auf Wunsch."""

    instances: ClassVar[list[FakeSMTP]] = []
    fail: ClassVar[dict[str, BaseException]] = {}

    def __init__(self, host: str, port: int, timeout: float, context: Any = None) -> None:
        self.calls: list[str] = []
        self.context = context
        self.args = (host, port, timeout)
        self.sent: list[EmailMessage] = []
        self._maybe("connect")
        FakeSMTP.instances.append(self)

    def _maybe(self, step: str) -> None:
        self.calls.append(step)
        if step in FakeSMTP.fail:
            raise FakeSMTP.fail[step]

    def starttls(self, context: Any) -> None:
        self.context = context
        self._maybe("starttls")

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)
        self._maybe("login")

    def send_message(self, msg: EmailMessage) -> None:
        self.sent.append(msg)
        self._maybe("send")

    def quit(self) -> None:
        self._maybe("quit")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    FakeSMTP.fail = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


def _settings(**kw: Any) -> MailSettings:
    base: dict[str, Any] = {
        "host": "mail.beispiel.test",
        "port": 587,
        "security": "starttls",
        "sender": "bericht@beispiel.test",
    }
    return MailSettings(**{**base, **kw})


def _msg() -> EmailMessage:
    return build_message("bericht@beispiel.test", ["a@beispiel.test"], "Betreff", "Text")


def _secrets(tmp_path: Path) -> Path:
    (tmp_path / "smtp_pw").write_text(SECRET_VALUE + "\n", encoding="utf-8")
    return tmp_path


def test_starttls_verifies_the_certificate_and_the_host_name(tmp_path: Path) -> None:
    send_message(_settings(), tmp_path, _msg())
    server = FakeSMTP.instances[0]
    assert server.calls == ["connect", "starttls", "send", "quit"]
    assert server.args == ("mail.beispiel.test", 587, 30)
    assert isinstance(server.context, ssl.SSLContext)
    assert server.context.verify_mode == ssl.CERT_REQUIRED
    assert server.context.check_hostname is True
    assert len(server.sent) == 1


def test_ssl_mode_uses_an_implicitly_encrypted_connection(tmp_path: Path) -> None:
    send_message(_settings(security="ssl", port=465), tmp_path, _msg())
    server = FakeSMTP.instances[0]
    assert server.calls == ["connect", "send", "quit"]  # kein starttls: schon verschlüsselt
    assert server.context.verify_mode == ssl.CERT_REQUIRED


def test_login_uses_the_secret_file(tmp_path: Path) -> None:
    cfg = _settings(user="bericht", password_secret="smtp_pw")
    send_message(cfg, _secrets(tmp_path), _msg())
    assert FakeSMTP.instances[0].login_args == ("bericht", SECRET_VALUE)
    assert FakeSMTP.instances[0].calls == ["connect", "starttls", "login", "send", "quit"]


def test_a_missing_secret_is_a_coded_error_without_connecting(tmp_path: Path) -> None:
    with pytest.raises(MailError) as err:
        send_message(_settings(user="u", password_secret="fehlt"), tmp_path, _msg())
    assert err.value.code == "secret"
    assert FakeSMTP.instances == []


@pytest.mark.parametrize(
    ("step", "exc", "code"),
    [
        ("connect", ConnectionRefusedError("a@beispiel.test abgelehnt"), "connect"),
        ("connect", smtplib.SMTPConnectError(421, "Dienst nicht da"), "connect"),
        ("starttls", ssl.SSLCertVerificationError("Zertifikat ungültig"), "tls"),
        ("login", smtplib.SMTPAuthenticationError(535, "Passwort falsch"), "auth"),
        ("send", smtplib.SMTPRecipientsRefused({"a@beispiel.test": (550, b"nein")}), "recipients"),
        ("send", smtplib.SMTPDataError(554, "abgelehnt"), "send"),
    ],
)
def test_errors_become_fixed_codes_that_leak_nothing(
    tmp_path: Path, step: str, exc: BaseException, code: str
) -> None:
    FakeSMTP.fail = {step: exc}
    cfg = _settings(user="bericht", password_secret="smtp_pw")
    with pytest.raises(MailError) as err:
        send_message(cfg, _secrets(tmp_path), _msg())
    assert err.value.code == code
    text = str(err.value) + repr(err.value) + "".join(map(str, err.value.args))
    assert "beispiel.test" not in text
    assert SECRET_VALUE not in text
    assert err.value.__cause__ is None  # kein angehängter Originalfehler mit Servertext


def test_a_failed_connection_closes_the_session(tmp_path: Path) -> None:
    FakeSMTP.fail = {"starttls": ssl.SSLError("x")}
    with pytest.raises(MailError):
        send_message(_settings(), tmp_path, _msg())
    assert "quit" in FakeSMTP.instances[0].calls


def test_an_unreadable_ca_file_is_a_tls_error(tmp_path: Path) -> None:
    with pytest.raises(MailError) as err:
        send_message(_settings(ca_file=tmp_path / "gibt-es-nicht.pem"), tmp_path, _msg())
    assert err.value.code == "tls"


def test_check_connection_logs_in_but_sends_nothing(tmp_path: Path) -> None:
    check_connection(_settings(user="bericht", password_secret="smtp_pw"), _secrets(tmp_path))
    server = FakeSMTP.instances[0]
    assert server.calls == ["connect", "starttls", "login", "quit"]
    assert server.sent == []


def test_quit_failure_still_closes(tmp_path: Path) -> None:
    FakeSMTP.fail = {"quit": smtplib.SMTPServerDisconnected("weg")}
    send_message(_settings(), tmp_path, _msg())
    assert FakeSMTP.instances[0].calls[-1] == "close"


def test_the_subject_cannot_inject_headers() -> None:
    msg = build_message(
        "bericht@beispiel.test",
        ["a@beispiel.test", "b@beispiel.test"],
        "Hallo\r\nBcc: boese@beispiel.test",
        "Text",
    )
    assert msg["Bcc"] is None
    assert "\n" not in msg["Subject"]
    assert msg["To"] == "a@beispiel.test, b@beispiel.test"
