"""Gemeinsame Bausteine der Seiten: Templates mit Filtern, Sitzung, Mandantensuche, Zeit."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Tenant
from app.web import labels

# Bis die Anmeldung steht (Phase 5), ist der Handelnde nicht bekannt.
ACTOR_UNAUTHENTICATED = "lokal (ohne Anmeldung)"

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["dt"] = labels.format_dt
templates.env.filters["status_label"] = labels.status_label
templates.env.filters["type_label"] = labels.account_type_label
templates.env.filters["source_label"] = labels.source_label
templates.env.filters["run_status_label"] = labels.run_status_label
templates.env.filters["warning_label"] = labels.warning_label


def db_session(request: Request) -> Session:
    """Neue Sitzung aus der Fabrik der Anwendung (der Aufrufer schließt sie)."""
    session: Session = request.app.state.session_factory()
    return session


def enabled_tenants(session: Session) -> list[Tenant]:
    """Alle aktiven Mandanten nach Kürzel."""
    return list(
        session.scalars(select(Tenant).where(Tenant.enabled.is_(True)).order_by(Tenant.slug))
    )


def tenant_or_404(session: Session, slug: str) -> Tenant:
    """Der Mandant mit diesem Kürzel oder 404."""
    tenant = session.scalars(select(Tenant).where(Tenant.slug == slug)).one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Mandant unbekannt")
    return tenant


def end_of_local_day_utc(day: date, tz: tzinfo) -> datetime:
    """Ende (23:59:59) des Tages in ``tz``, als UTC. Ein Ablaufdatum gilt den ganzen Tag."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=tz).astimezone(UTC)


def local_date(moment: datetime, tz: tzinfo) -> date:
    """Kalendertag eines Zeitpunkts in ``tz``."""
    return moment.astimezone(tz).date()
