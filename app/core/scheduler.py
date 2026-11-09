"""Zeitplan: täglicher Abruf, nächtliche Sicherung, Wochenbericht (APScheduler)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.backup.service import run_external_backup
from app.config import Settings
from app.core.backup import run_backup
from app.core.cycle import run_nightly
from app.core.report import run_weekly_reports

log = logging.getLogger(__name__)

JOB_ID = "nightly"
BACKUP_JOB_ID = "backup"
REPORT_JOB_ID = "report"
EXTERNAL_BACKUP_JOB_ID = "external_backup"
_MISFIRE_GRACE_SECONDS = 3600  # Neustart kurz nach der Zeit: noch nachholen


def create_scheduler(factory: sessionmaker[Session], settings: Settings) -> BackgroundScheduler:
    """Baut den Zeitplan (startet ihn nicht): Abruf täglich um ``sync_time``, Sicherung um
    ``backup_time``, bei konfiguriertem Mailversand der Wochenbericht (jeweils in ``timezone``).

    ``max_instances=1`` und ``coalesce`` verhindern gestapelte Läufe nach einem Ausfall.
    """
    hour, minute = settings.sync_hour_minute
    scheduler = BackgroundScheduler(timezone=settings.tzinfo)
    scheduler.add_job(
        run_nightly,
        CronTrigger(hour=hour, minute=minute, timezone=settings.tzinfo),
        args=[factory, settings],
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    backup_hour, backup_minute = settings.backup_hour_minute
    scheduler.add_job(
        run_backup,
        CronTrigger(hour=backup_hour, minute=backup_minute, timezone=settings.tzinfo),
        args=[factory, settings],
        id=BACKUP_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    if settings.mail is not None:
        report_hour, report_minute = settings.mail.report_hour_minute
        scheduler.add_job(
            run_weekly_reports,
            CronTrigger(
                day_of_week=settings.mail.report_weekday,
                hour=report_hour,
                minute=report_minute,
                timezone=settings.tzinfo,
            ),
            args=[factory, settings],
            id=REPORT_JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
            replace_existing=True,
        )
    if settings.external_backup is not None:
        ext_hour, ext_minute = (int(part) for part in settings.external_backup.time.split(":"))
        scheduler.add_job(
            run_external_backup,
            CronTrigger(hour=ext_hour, minute=ext_minute, timezone=settings.tzinfo),
            args=[factory, settings],
            id=EXTERNAL_BACKUP_JOB_ID,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
            replace_existing=True,
        )
    return scheduler
