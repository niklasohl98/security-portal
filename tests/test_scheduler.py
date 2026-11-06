from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.cycle import run_nightly
from app.core.scheduler import JOB_ID, create_scheduler
from app.main import create_app


def _settings(base: Settings, **kw: object) -> Settings:
    from dataclasses import replace

    return replace(base, **kw)  # type: ignore[arg-type]


def test_scheduler_has_one_daily_job_with_safe_options(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    cfg = _settings(settings, sync_time="02:30", timezone="Europe/Berlin")
    scheduler = create_scheduler(factory, cfg)
    job = scheduler.get_job(JOB_ID)
    assert job is not None
    assert job.func is run_nightly
    assert (job.max_instances, job.coalesce) == (1, True)
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("2", "30")
    # 02:30 Berlin im Winter = 01:30 UTC
    fire = job.trigger.get_next_fire_time(None, datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    assert fire is not None
    assert fire.astimezone(UTC) == datetime(2026, 11, 7, 1, 30, tzinfo=UTC)


def test_scheduler_is_not_started_by_default(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.app.state.scheduler is None  # type: ignore[attr-defined]


def test_scheduler_starts_with_the_app_and_stops_with_it(settings: Settings) -> None:
    cfg = _settings(settings, scheduler_enabled=True)
    with TestClient(create_app(cfg)) as client:
        scheduler = client.app.state.scheduler  # type: ignore[attr-defined]
        assert scheduler.running is True
        assert scheduler.get_job(JOB_ID) is not None
    assert scheduler.running is False
