"""SMTP-Versand mit Zertifikatsprüfung. Fehler nennen nur einen festen Code.

Weder Log noch Fehlertext enthalten Adressen, Servernachrichten oder das Passwort: smtplib-Meldungen
können Empfängeradressen und Antworttexte des Servers enthalten, darum wird nur die Art des Fehlers
weitergegeben.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from app.config import ConfigError, read_secret
from app.mail.settings import MailSettings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


class MailError(Exception):
    """Versand nicht möglich. ``code``: ``secret``, ``tls``, ``connect``, ``auth``, ``recipients``,
    ``send``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_message(sender: str, recipients: list[str], subject: str, text: str) -> EmailMessage:
    """Baut eine Textmail. Der Betreff verliert Zeilenumbrüche (kein Header-Einschub)."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = " ".join(subject.split())
    msg.set_content(text)
    return msg


def _context(settings: MailSettings) -> ssl.SSLContext:
    """Standardkontext mit Zertifikats- und Hostnamenprüfung, optional mit zusätzlicher CA."""
    try:
        return ssl.create_default_context(
            cafile=str(settings.ca_file) if settings.ca_file else None
        )
    except (OSError, ssl.SSLError):
        raise MailError("tls") from None


def _open(settings: MailSettings, secrets_dir: Path) -> smtplib.SMTP:
    """Verbindet (verschlüsselt) und meldet an, falls ein Benutzer konfiguriert ist."""
    password = None
    if settings.password_secret:
        try:
            password = read_secret(settings.password_secret, secrets_dir)
        except ConfigError:
            raise MailError("secret") from None
    ctx = _context(settings)
    server: smtplib.SMTP | None = None
    try:
        if settings.security == "ssl":
            server = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=TIMEOUT_SECONDS, context=ctx
            )
        else:
            server = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS)
            server.starttls(context=ctx)
        if settings.user and password:
            server.login(settings.user, password)
    except ssl.SSLError:
        _close(server)
        raise MailError("tls") from None
    except smtplib.SMTPAuthenticationError:
        _close(server)
        raise MailError("auth") from None
    except (smtplib.SMTPException, OSError):
        _close(server)
        raise MailError("connect") from None
    return server


def _close(server: smtplib.SMTP | None) -> None:
    if server is None:
        return
    try:
        server.quit()
    except (smtplib.SMTPException, OSError):
        server.close()


def check_connection(settings: MailSettings, secrets_dir: Path) -> None:
    """Prüft Verbindung, Zertifikat und Anmeldung, ohne eine Mail zu senden.

    Raises:
        MailError: mit festem Code.
    """
    _close(_open(settings, secrets_dir))


def send_message(settings: MailSettings, secrets_dir: Path, msg: EmailMessage) -> None:
    """Sendet die Mail.

    Raises:
        MailError: mit festem Code (``recipients``, wenn der Server alle Empfänger ablehnt).
    """
    server = _open(settings, secrets_dir)
    try:
        server.send_message(msg)
    except smtplib.SMTPRecipientsRefused:
        raise MailError("recipients") from None
    except (smtplib.SMTPException, OSError):
        raise MailError("send") from None
    finally:
        _close(server)
