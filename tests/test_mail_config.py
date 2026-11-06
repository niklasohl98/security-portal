"""Mail-Einstellungen, Empfänger je Mandant und Zeitplan des Wochenberichts."""

from __future__ import annotations

from dataclasses import replace

import pytest
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import ConfigError, ReportConfig, Settings
from app.core.report import run_weekly_reports
from app.core.scheduler import REPORT_JOB_ID, create_scheduler
from app.core.tenants import sync_tenants
from app.mail.settings import MailSettings
from tests.identity_fakes import get_tenant

BASE_ENV = {"KW_SMTP_HOST": "mail.beispiel.test", "KW_MAIL_FROM": "bericht@beispiel.test"}


def test_mail_is_off_without_a_host() -> None:
    assert MailSettings.from_env({}) is None
    assert MailSettings.from_env({"KW_SMTP_HOST": "  "}) is None


def test_blank_variables_count_as_unset() -> None:
    """``docker-compose.yml`` reicht leere Werte durch (``${KW_SMTP_PORT:-}``)."""
    blank = {
        "KW_SMTP_PORT": "",
        "KW_SMTP_SECURITY": " ",
        "KW_SMTP_USER": "",
        "KW_SMTP_PASSWORD_SECRET": "",
        "KW_SMTP_CA_FILE": "",
        "KW_REPORT_WEEKDAY": "",
        "KW_REPORT_TIME": "",
    }
    mail = MailSettings.from_env({**BASE_ENV, **blank})
    assert mail == MailSettings.from_env(BASE_ENV)
    assert MailSettings.from_env({"KW_SMTP_HOST": "", "KW_MAIL_FROM": ""}) is None


def test_defaults_follow_the_security_mode() -> None:
    plain = MailSettings.from_env(BASE_ENV)
    assert plain is not None
    assert (plain.port, plain.security, plain.user) == (587, "starttls", None)
    assert (plain.report_weekday, plain.report_hour_minute) == ("mon", (7, 30))
    tls = MailSettings.from_env({**BASE_ENV, "KW_SMTP_SECURITY": "SSL"})
    assert tls is not None
    assert (tls.port, tls.security) == (465, "ssl")


def test_full_configuration_is_read(settings: Settings) -> None:
    mail = MailSettings.from_env(
        {
            **BASE_ENV,
            "KW_SMTP_PORT": "2525",
            "KW_SMTP_USER": "bericht",
            "KW_SMTP_PASSWORD_SECRET": "smtp_password",
            "KW_SMTP_CA_FILE": "/config/ca.pem",
            "KW_REPORT_WEEKDAY": "Fri",
            "KW_REPORT_TIME": "06:05",
        }
    )
    assert mail is not None
    assert (mail.port, mail.user, mail.password_secret) == (2525, "bericht", "smtp_password")
    assert (mail.report_weekday, mail.report_hour_minute) == ("fri", (6, 5))
    assert str(mail.ca_file) == "/config/ca.pem"
    env = {"KW_DATA_DIR": str(settings.data_dir), **BASE_ENV}
    assert Settings.from_env(env).mail is not None
    assert Settings.from_env({"KW_DATA_DIR": str(settings.data_dir)}).mail is None


@pytest.mark.parametrize(
    ("override", "name"),
    [
        ({"KW_SMTP_HOST": "mail beispiel"}, "KW_SMTP_HOST"),
        ({"KW_SMTP_SECURITY": "none"}, "KW_SMTP_SECURITY"),
        ({"KW_SMTP_PORT": "70000"}, "KW_SMTP_PORT"),
        ({"KW_SMTP_PORT": "abc"}, "KW_SMTP_PORT"),
        ({"KW_MAIL_FROM": "kein-at"}, "KW_MAIL_FROM"),
        ({"KW_SMTP_USER": "nutzer-ohne-passwort"}, "KW_SMTP_USER"),
        ({"KW_SMTP_PASSWORD_SECRET": "../x"}, "KW_SMTP_PASSWORD_SECRET"),
        ({"KW_REPORT_WEEKDAY": "montag"}, "KW_REPORT_WEEKDAY"),
        ({"KW_REPORT_TIME": "25:00"}, "KW_REPORT_TIME"),
    ],
)
def test_invalid_values_name_the_variable_but_never_the_value(
    override: dict[str, str], name: str
) -> None:
    with pytest.raises(ConfigError) as err:
        MailSettings.from_env({**BASE_ENV, **override})
    assert name in str(err.value)
    assert next(iter(override.values())) not in str(err.value)


def test_recipients_are_validated() -> None:
    assert ReportConfig(recipients=[" a@beispiel.test "]).recipients == ["a@beispiel.test"]
    for bad in (["kein-at"], ["a b@beispiel.test"], ["a@x.test", "A@X.test"], ["<a@x.test>"]):
        with pytest.raises(ValidationError):
            ReportConfig(recipients=bad)
    with pytest.raises(ValidationError):
        ReportConfig(recipients=[f"u{n}@beispiel.test" for n in range(21)])


def test_recipients_come_from_the_yaml_into_the_database(
    factory: sessionmaker[Session],
) -> None:
    from app.config import load_tenants
    from tests.conftest import EXAMPLE_TENANTS

    configs = load_tenants(EXAMPLE_TENANTS)
    configs[0] = configs[0].model_copy(
        update={"report": ReportConfig(recipients=["it@beispiel.test"])}
    )
    with factory.begin() as s:
        sync_tenants(s, configs)
    with factory() as s:
        assert get_tenant(s, configs[0].slug).report_recipients == ["it@beispiel.test"]


def test_weekly_job_exists_only_with_mail(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    assert create_scheduler(factory, settings).get_job(REPORT_JOB_ID) is None
    mail = MailSettings.from_env(
        {**BASE_ENV, "KW_REPORT_WEEKDAY": "wed", "KW_REPORT_TIME": "08:15"}
    )
    job = create_scheduler(factory, replace(settings, mail=mail)).get_job(REPORT_JOB_ID)
    assert job is not None
    assert job.func is run_weekly_reports
    assert (job.max_instances, job.coalesce) == (1, True)
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["day_of_week"], fields["hour"], fields["minute"]) == ("wed", "8", "15")
