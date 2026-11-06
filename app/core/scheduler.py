"""Zeitplan: täglicher Abruf aller Mandanten und nächtliche Sicherung (APScheduler)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.backup import run_backup
from app.core.cycle import run_nightly

log = logging.getLogger(__name__)

JOB_ID = "nightly"
BACKUP_JOB_ID = "backup"
_MISFIRE_GRACE_SECONDS = 3600  # Neustart kurz nach der Zeit: noch nachholen


def create_scheduler(factory: sessionmaker[Session], settings: Settings) -> BackgroundScheduler:
    """Baut den Zeitplan (startet ihn nicht): Abruf täglich um ``sync_time``, Sicherung um
    ``backup_time`` (jeweils in ``timezone``).

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
    return scheduler
