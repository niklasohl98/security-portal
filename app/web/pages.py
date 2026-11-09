"""Seiten: Übersicht, Kontenliste, Kontodetail (mit Ausnahme anlegen und Kontoart setzen)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.context import actor_name
from app.backup.service import external_status
from app.config import Settings
from app.core.accounts import AccountRow, apply_filter, get_account, load_accounts
from app.core.backup import backup_status
from app.core.exceptions import (
    ExceptionError,
    ExceptionItem,
    create_exception,
    default_valid_until,
    list_exceptions,
)
from app.core.overrides import OverrideError, set_account_type
from app.core.overview import tenant_overview
from app.core.rules import ad_disabled
from app.db.models import utcnow
from app.web.common import (
    db_session,
    enabled_tenants,
    end_of_local_day_utc,
    local_date,
    templates,
    tenant_or_404,
)
from app.web.filters import parse_account_query
from app.web.labels import ACCOUNT_TYPE_LABELS, SOURCE_LABELS, STATUS_LABELS
from app.web.security import form_fields, require_same_origin

log = logging.getLogger(__name__)

PAGE_SIZE = 200

# Meldungen nur aus diesen Listen, nie aus Eingaben.
_DONE = {
    "exception": "Ausnahme angelegt.",
    "type": "Kontoart gespeichert.",
}
_ERRORS = {
    "reason_required": "Bitte einen Grund angeben.",
    "reason_too_long": "Der Grund ist zu lang (höchstens 500 Zeichen).",
    "not_in_future": "Das Ablaufdatum muss in der Zukunft liegen.",
    "already_valid": "Für dieses Konto gibt es bereits eine gültige Ausnahme.",
    "bad_date": "Bitte ein gültiges Ablaufdatum angeben.",
    "bad_type": "Unbekannte Kontoart.",
    "not_found": "Das Konto wurde nicht gefunden.",
}

router = APIRouter()


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/", response_class=HTMLResponse)
def overview(request: Request) -> Response:
    """Übersicht: je Mandant Kennzahlen, freiwerdende Lizenzen, letzte Läufe und Warnungen."""
    now = utcnow()
    settings = _settings(request)
    with db_session(request) as session:
        data = [
            tenant_overview(session, t, load_accounts(session, t, now), now)
            for t in enabled_tenants(session)
        ]
        backup = backup_status(session, settings, now)
        external = external_status(session, settings, now)
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "tenants": data,
            "backup": backup,
            "warn_hours": settings.backup_warn_hours,
            "external_backup": external,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
def accounts(request: Request) -> Response:
    """Kontenliste mit Filtern, sortiert nach Tagen seit der letzten Anmeldung."""
    query = parse_account_query(request.query_params)
    now = utcnow()
    with db_session(request) as session:
        tenants = enabled_tenants(session)
        rows: list[AccountRow] = []
        for tenant in tenants:
            if query.tenant in (None, tenant.slug):
                rows.extend(load_accounts(session, tenant, now))
        tenant_options = [(t.slug, t.name) for t in tenants]
    shown = apply_filter(rows, query.filter)
    pages = max(1, -(-len(shown) // PAGE_SIZE))
    page = min(query.page, pages)
    start = (page - 1) * PAGE_SIZE
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "rows": shown[start : start + PAGE_SIZE],
            "total": len(shown),
            "page": page,
            "pages": pages,
            "q": query,
            "tenants": tenant_options,
            "statuses": {k: v for k, v in STATUS_LABELS.items() if k != "missing"},
            "types": ACCOUNT_TYPE_LABELS,
            "sources": SOURCE_LABELS,
            "export_qs": query.query_string(page=None),
            "prev_qs": query.query_string(page=page - 1 if page > 2 else None),
            "next_qs": query.query_string(page=page + 1),
        },
    )


def _detail_context(
    request: Request, slug: str, identity_id: int, done: str | None, err: str | None
) -> dict[str, object]:
    cfg = _settings(request)
    now = utcnow()
    with db_session(request) as session:
        tenant = tenant_or_404(session, slug)
        row = get_account(session, tenant, identity_id, now)
        if row is None:
            raise HTTPException(status_code=404, detail="Konto nicht gefunden")
        exceptions: list[ExceptionItem] = [
            e for e in list_exceptions(session, tenant, now) if e.identity_id == identity_id
        ]
        default_until = local_date(default_valid_until(tenant, now), cfg.tzinfo)
    dc_logons = sorted(
        (host, datetime.fromisoformat(value) if value else None)
        for host, value in (row.ad.last_logon_per_dc.items() if row.ad else [])
    )
    return {
        "row": row,
        "ad_disabled": bool(row.ad and ad_disabled(row.ad.user_account_control)),
        "dc_logons": dc_logons,
        "exceptions": exceptions,
        "default_until": default_until.isoformat(),
        "types": ACCOUNT_TYPE_LABELS,
        "message": _DONE.get(done or ""),
        "error": _ERRORS.get(err or ""),
    }


@router.get("/accounts/{slug}/{identity_id}", response_class=HTMLResponse)
def account_detail(
    request: Request,
    slug: str,
    identity_id: int,
    done: str | None = None,
    err: str | None = None,
) -> Response:
    """Kontodetail: alle Anmeldewerte nebeneinander, Lizenzen, Ausnahmen, manuelle Kontoart."""
    context = _detail_context(request, slug, identity_id, done, err)
    return templates.TemplateResponse(request, "account_detail.html", context)


def _back(slug: str, identity_id: int, **params: str) -> RedirectResponse:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"/accounts/{slug}/{identity_id}?{query}", status_code=303)


@router.post(
    "/accounts/{slug}/{identity_id}/exception", dependencies=[Depends(require_same_origin)]
)
def post_exception(
    request: Request,
    slug: str,
    identity_id: int,
    fields: Annotated[dict[str, str], Depends(form_fields)],
) -> RedirectResponse:
    """Legt eine Ausnahme an (Grund und Ablaufdatum ``JJJJ-MM-TT``, gilt bis Tagesende)."""
    cfg = _settings(request)
    try:
        until = end_of_local_day_utc(date.fromisoformat(fields.get("valid_until", "")), cfg.tzinfo)
    except ValueError:
        return _back(slug, identity_id, err="bad_date")
    with db_session(request) as session, session.begin():
        tenant = tenant_or_404(session, slug)
        try:
            create_exception(
                session, tenant, identity_id, fields.get("reason", ""), until, actor_name(request)
            )
        except ExceptionError as exc:
            return _back(slug, identity_id, err=exc.code if exc.code in _ERRORS else "not_found")
    return _back(slug, identity_id, done="exception")


@router.post("/accounts/{slug}/{identity_id}/type", dependencies=[Depends(require_same_origin)])
def post_account_type(
    request: Request,
    slug: str,
    identity_id: int,
    fields: Annotated[dict[str, str], Depends(form_fields)],
) -> RedirectResponse:
    """Setzt die manuelle Kontoart; leer = zurück zur automatischen."""
    with db_session(request) as session, session.begin():
        tenant = tenant_or_404(session, slug)
        try:
            set_account_type(
                session, tenant, identity_id, fields.get("account_type"), actor_name(request)
            )
        except OverrideError as exc:
            return _back(slug, identity_id, err=exc.code)
    return _back(slug, identity_id, done="type")
