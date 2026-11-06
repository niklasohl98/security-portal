"""Wochenbericht: Inhalt (nur Kennzahlen), Versand, Audit-Log, Wochenjob."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.report import ReportError, build_report, run_weekly_reports, send_tenant_report
from app.db.models import AuditLog, RunStatus, Source, SyncRun
from app.mail.settings import MailSettings
from app.mail.transport import MailError
from tests.identity_fakes import NOW, get_tenant, make_run, seed_world

MAIL = MailSettings(
    host="mail.beispiel.test", port=587, security="starttls", sender="bericht@beispiel.test"
)
RECIPIENTS = ["it@beispiel.test", "leitung@beispiel.test"]


class Outbox:
    """Fängt ``send_message`` ab."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.error: MailError | None = None

    def __call__(self, _settings: object, _secrets: object, msg: EmailMessage) -> None:
        if self.error:
            raise self.error
        self.sent.append((str(msg["To"]), str(msg["Subject"]), str(msg.get_content())))


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr("app.core.report.send_message", box)
    return box


@pytest.fixture
def world(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with factory.begin() as s:
        seed_world(s)
        get_tenant(s).report_recipients = RECIPIENTS
        for run in s.scalars(select(SyncRun)):
            run.finished_at = NOW
    return factory


def _cfg(settings: Settings) -> Settings:
    return replace(settings, mail=MAIL)


def test_the_report_contains_counts_and_product_names_but_no_accounts(
    world: sessionmaker[Session],
) -> None:
    with world() as s:
        report = build_report(s, get_tenant(s), NOW)
    text = report.text
    assert report.subject.startswith("Kontenwächter Wochenbericht ")
    assert report.recipients == RECIPIENTS
    assert "Konten gesamt: 4" in text
    assert "(markiert): 3" in text  # Person 1, Person 2, nur.ad
    assert "mit bezahlter Lizenz: 2" in text  # Person 1 (E3) und Person 2 (unbekannte SKU)
    assert "  - Microsoft 365 E3: 1" in text
    assert "  - NEU_X: 1" in text  # unbekannte SKU: Teilenummer als Name
    assert "Flow Free" not in text  # kostenlos
    assert "Verschwundene Konten zur Prüfung: 0" in text
    assert "Entra:" in text
    assert "AD:" in text
    # Keine Konten: weder Anzeigenamen noch UPN noch AD-Name.
    for forbidden in ("Person", "user1@", "nur.ad", "@beispiel.test"):
        assert forbidden not in text


def test_the_report_warns_about_old_or_missing_data(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        empty = build_report(s, get_tenant(s), NOW)
    assert "ACHTUNG" in empty.text
    with factory.begin() as s:
        t = get_tenant(s)
        run = make_run(s, t, Source.ENTRA)
        run.finished_at = NOW - timedelta(days=3)
        run_id = run.id
    with factory() as s:
        assert "ACHTUNG" in build_report(s, get_tenant(s), NOW).text
    with factory.begin() as s:
        s.get_one(SyncRun, run_id).finished_at = NOW - timedelta(hours=5)
    with factory() as s:
        assert "ACHTUNG" not in build_report(s, get_tenant(s), NOW).text


def test_warnings_and_failed_runs_are_listed(world: sessionmaker[Session]) -> None:
    with world.begin() as s:
        t = get_tenant(s)
        make_run(s, t, Source.AD, RunStatus.FAILED, ["ad_dc_unreachable"]).finished_at = NOW
    with world() as s:
        text = build_report(s, get_tenant(s), NOW).text
    assert "Hinweise:" in text
    assert "Ein Domänencontroller war nicht erreichbar" in text
    assert "Der jüngste Abruf ist fehlgeschlagen" in text


def test_sending_delivers_to_all_recipients_and_audits_only_the_count(
    world: sessionmaker[Session], settings: Settings, outbox: Outbox
) -> None:
    count = send_tenant_report(world, _cfg(settings), "cg", actor="Tester", now=NOW)
    assert count == 2
    to, subject, body = outbox.sent[0]
    assert to == ", ".join(RECIPIENTS)
    assert "Wochenbericht" in subject
    assert "Konten gesamt" in body
    with world() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "report.sent")).one()
    assert (entry.actor, entry.detail) == ("Tester", "recipients=2")
    assert entry.tenant_id is not None
    assert "@" not in (entry.detail or "")


def test_a_failed_send_is_audited_with_the_code_only(
    world: sessionmaker[Session],
    settings: Settings,
    outbox: Outbox,
    caplog: pytest.LogCaptureFixture,
) -> None:
    outbox.error = MailError("auth")
    with caplog.at_level("INFO"), pytest.raises(MailError):
        send_tenant_report(world, _cfg(settings), "cg", actor="Tester", now=NOW)
    with world() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "report.failed")).one()
        assert s.scalars(select(AuditLog).where(AuditLog.action == "report.sent")).first() is None
    assert entry.detail == "error=auth"
    assert "@" not in caplog.text
    assert "auth" in caplog.text


@pytest.mark.parametrize(
    ("slug", "mail", "code"),
    [
        ("cg", None, "not_configured"),
        ("gibt-es-nicht", MAIL, "unknown_tenant"),
        ("cvh", MAIL, "no_recipients"),
    ],
)
def test_report_errors_are_coded(
    world: sessionmaker[Session],
    settings: Settings,
    outbox: Outbox,
    slug: str,
    mail: MailSettings | None,
    code: str,
) -> None:
    with pytest.raises(ReportError) as err:
        send_tenant_report(world, replace(settings, mail=mail), slug, actor="x")
    assert err.value.code == code
    assert outbox.sent == []


def test_the_weekly_job_sends_only_where_recipients_exist_and_survives_errors(
    world: sessionmaker[Session], settings: Settings, outbox: Outbox
) -> None:
    run_weekly_reports(world, _cfg(settings))  # cvh hat keine Empfänger
    assert len(outbox.sent) == 1
    with world() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "report.sent")).one()
    assert entry.actor == "system"
    with world.begin() as s:
        cvh = get_tenant(s, "cvh")
        cvh.report_recipients = ["x@beispiel.test"]
    outbox.error = MailError("connect")
    run_weekly_reports(world, _cfg(settings))  # beide scheitern, der Job läuft durch
    with world() as s:
        failed = s.scalars(select(AuditLog).where(AuditLog.action == "report.failed")).all()
    assert len(failed) == 2


def test_the_weekly_job_does_nothing_without_mail(
    world: sessionmaker[Session], settings: Settings, outbox: Outbox
) -> None:
    run_weekly_reports(world, settings)
    assert outbox.sent == []
