"""Seite „Berichte“ und Befehle ``mail-check``, ``report-preview``, ``report-send``."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import service
from app.cli import main as cli_main
from app.config import Settings
from app.db.models import AppUser, AuditLog
from app.mail.settings import MailSettings
from app.mail.transport import MailError
from tests.conftest import AuthedClient
from tests.identity_fakes import NOW, get_tenant, seed_world
from tests.web_fakes import open_session, post_form

MAIL = MailSettings(
    host="mail.beispiel.test",
    port=587,
    security="starttls",
    sender="bericht@beispiel.test",
    user="bericht",
    password_secret="smtp_pw",
)


@pytest.fixture
def mail_site(site: AuthedClient, monkeypatch: pytest.MonkeyPatch) -> AuthedClient:
    """Angemeldeter Admin; Mailversand eingerichtet, Empfänger bei cg, Versand abgefangen."""
    state = cast(FastAPI, site.app).state
    state.settings = replace(state.settings, mail=MAIL)
    with open_session(site) as s, s.begin():
        seed_world(s)
        get_tenant(s).report_recipients = ["it@beispiel.test"]
    site.sent = []
    monkeypatch.setattr(
        "app.core.report.send_message",
        lambda _s, _d, msg: site.sent.append(msg),
    )
    return site


def test_the_page_shows_the_mail_setup_and_the_tenants(mail_site: AuthedClient) -> None:
    page = mail_site.get("/reports").text
    assert "mail.beispiel.test:587" in page
    assert "STARTTLS" in page
    assert "Montag 07:30" in page
    assert "bericht@beispiel.test" in page
    assert 'action="/reports/cg/send"' in page
    assert 'action="/reports/cvh/send"' not in page  # keine Empfänger
    assert 'href="/reports"' in mail_site.get("/").text


def test_the_page_without_mail_explains_and_still_previews(site: AuthedClient) -> None:
    with open_session(site) as s, s.begin():
        seed_world(s)
    page = site.get("/reports").text
    assert "Nicht eingerichtet" in page
    assert "/send" not in page
    assert "Konten gesamt: 4" in site.get("/reports/cg").text


def test_preview_shows_the_text_and_404s_for_unknown_tenants(mail_site: AuthedClient) -> None:
    page = mail_site.get("/reports/cg").text
    assert "Wochenbericht" in page
    assert "Empfänger: 1" in page
    assert "Person 1" not in page
    assert mail_site.get("/reports/gibt-es-nicht").status_code == 404


def test_send_now_sends_audits_and_confirms(mail_site: AuthedClient) -> None:
    r = post_form(mail_site, "/reports/cg/send")
    assert r.headers["location"] == "/reports?done=sent"
    assert "Bericht gesendet" in mail_site.get("/reports?done=sent").text
    assert len(mail_site.sent) == 1
    with open_session(mail_site) as s:
        actions = [(a.actor, a.action, a.detail) for a in s.query(AuditLog)]
    assert ("Tester", "report.sent", "recipients=1") in actions


@pytest.mark.parametrize(
    ("slug", "code"), [("cvh", "no_recipients"), ("gibt-es-nicht", "unknown_tenant")]
)
def test_send_errors_redirect_with_a_fixed_message(
    mail_site: AuthedClient, slug: str, code: str
) -> None:
    assert post_form(mail_site, f"/reports/{slug}/send").headers["location"] == (
        f"/reports?err={code}"
    )
    assert mail_site.sent == []


def test_a_mail_failure_shows_the_code_text(
    mail_site: AuthedClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object) -> None:
        raise MailError("tls")

    monkeypatch.setattr("app.core.report.send_message", boom)
    r = post_form(mail_site, "/reports/cg/send")
    assert r.headers["location"] == "/reports?err=tls"
    text = mail_site.get("/reports?err=tls").text
    assert "TLS-Fehler" in text
    assert "beispiel.test:587" in text  # Seite zeigt Konfiguration, aber keine Servermeldung


def test_the_connection_check(mail_site: AuthedClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.reports_page.check_connection", lambda *_a: None)
    assert post_form(mail_site, "/reports/check").headers["location"] == "/reports?done=check_ok"
    assert "in Ordnung" in mail_site.get("/reports?done=check_ok").text

    def boom(*_a: object) -> None:
        raise MailError("auth")

    monkeypatch.setattr("app.web.reports_page.check_connection", boom)
    assert post_form(mail_site, "/reports/check").headers["location"] == "/reports?err=auth"


def test_without_mail_the_actions_report_not_configured(site: AuthedClient) -> None:
    with open_session(site) as s, s.begin():
        seed_world(s)
    assert post_form(site, "/reports/check").headers["location"] == "/reports?err=not_configured"
    assert post_form(site, "/reports/cg/send").headers["location"] == "/reports?err=not_configured"


def test_only_admins_with_a_csrf_token_may_use_the_pages(
    mail_site: AuthedClient, settings: Settings
) -> None:
    assert post_form(mail_site, "/reports/cg/send", csrf=False).status_code == 403
    assert post_form(mail_site, "/reports/check", csrf=False).status_code == 403
    with open_session(mail_site) as s, s.begin():
        uid = service.create_user(s, "Mitglied", "Ein-gutes-Passwort-2", "test", NOW)
        s.get_one(AppUser, uid).must_change_password = False
        new = service.create_session(s, settings, uid, NOW)
    mail_site.cookies.set("kw_session", new.token)
    assert mail_site.get("/reports").status_code == 403
    assert mail_site.get("/reports/cg").status_code == 403
    for path in ("/reports/cg/send", "/reports/check"):
        r = mail_site.post(
            path, data={"csrf_token": new.csrf_token}, headers={"Origin": "http://testserver"}
        )
        assert r.status_code == 403, path
    assert mail_site.sent == []
    assert 'href="/reports"' not in mail_site.get("/").text


def test_anonymous_users_are_sent_to_the_login(anon: TestClient) -> None:
    r = anon.get("/reports", follow_redirects=False)
    assert (r.status_code, r.headers["location"].startswith("/login")) == (303, True)


# --- Befehlszeile ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    from app.main import create_app

    with TestClient(create_app(settings)) as client, open_session(client) as s, s.begin():
        seed_world(s)
        get_tenant(s).report_recipients = ["it@beispiel.test", "x@beispiel.test"]
    monkeypatch.setenv("KW_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("KW_SECRETS_DIR", str(settings.secrets_dir))
    return settings


def _mail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_SMTP_HOST", "mail.beispiel.test")
    monkeypatch.setenv("KW_MAIL_FROM", "bericht@beispiel.test")


def test_cli_preview_prints_the_report_without_sending(
    cli_env: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["report-preview", "cg"]) == 0
    out = capsys.readouterr().out
    assert "Empfänger: 2" in out
    assert "Konten gesamt: 4" in out
    assert "@" not in out
    assert cli_main(["report-preview", "nix"]) == 1


def test_cli_send_and_errors(
    cli_env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["report-send", "cg"]) == 1
    assert "not_configured" in capsys.readouterr().out
    _mail_env(monkeypatch)
    sent: list[object] = []
    monkeypatch.setattr("app.core.report.send_message", lambda _s, _d, msg: sent.append(msg))
    assert cli_main(["report-send", "cg"]) == 0
    assert "2 Empfänger" in capsys.readouterr().out
    assert len(sent) == 1

    def boom(*_a: object) -> None:
        raise MailError("connect")

    monkeypatch.setattr("app.core.report.send_message", boom)
    assert cli_main(["report-send", "cg"]) == 1
    out = capsys.readouterr().out
    assert "connect" in out
    assert "@" not in out


def test_cli_mail_check(
    cli_env: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["mail-check"]) == 1
    assert "nicht eingerichtet" in capsys.readouterr().out
    _mail_env(monkeypatch)
    monkeypatch.setattr("app.cli_mail.check_connection", lambda *_a: None)
    assert cli_main(["mail-check"]) == 0

    def boom(*_a: object) -> None:
        raise MailError("tls")

    monkeypatch.setattr("app.cli_mail.check_connection", boom)
    assert cli_main(["mail-check"]) == 1
    assert "tls" in capsys.readouterr().out
