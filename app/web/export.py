"""Export der aktuellen Filteransicht als CSV oder XLSX (``GET /export``).

Nimmt dieselben Parameter wie ``/accounts`` plus ``format`` (``csv`` oder ``xlsx``); die Seite
(``page``) spielt keine Rolle, exportiert wird immer die ganze Ansicht. Jeder Export steht im
Audit-Log (Format, Zeilenzahl, gewählte Filter; nie der Suchtext). Die Anfrage verlangt wie die
ändernden Routen Same-Origin, damit keine fremde Seite Exporte auslösen kann.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.context import actor_name
from app.config import Settings
from app.core.accounts import AccountRow, apply_filter, load_accounts
from app.db.models import AuditLog, Tenant, utcnow
from app.export.csv import build_csv
from app.export.xlsx import ExportInfo, TenantInfo, build_xlsx
from app.web.common import db_session, enabled_tenants, local_date
from app.web.filters import AccountQuery, parse_account_query
from app.web.labels import ACCOUNT_TYPE_LABELS, SOURCE_LABELS, STATUS_LABELS
from app.web.security import require_same_origin

log = logging.getLogger(__name__)

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")

router = APIRouter()


def _yes_no(value: bool | None, unset: str) -> str:
    return unset if value is None else ("ja" if value else "nein")


def _file_name(tenants: list[Tenant], day: str, extension: str) -> str:
    """``Inaktive_Konten_<Mandant>_<JJJJ-MM-TT>.<ext>``; ``Alle`` bei mehreren Mandanten."""
    who = _UNSAFE_NAME.sub("_", tenants[0].name).strip("_") if len(tenants) == 1 else "Alle"
    return f"Inaktive_Konten_{who or 'Mandant'}_{day}.{extension}"


def _filter_lines(query: AccountQuery, tenants: list[Tenant]) -> list[tuple[str, str]]:
    f = query.filter
    return [
        ("Mandant", tenants[0].name if len(tenants) == 1 else "Alle"),
        ("Anzeige", "Nur geflaggte Konten" if f.flagged_only else "Alle Konten"),
        ("Status", STATUS_LABELS.get(f.status or "", "alle")),
        ("Kontoart", ACCOUNT_TYPE_LABELS.get(f.account_type or "", "alle")),
        ("Lizenziert", _yes_no(f.licensed, "egal")),
        ("Quelle", SOURCE_LABELS.get(f.source or "", "alle")),
        ("Suche", f.query or "-"),
    ]


def _audit_detail(fmt: str, count: int, query: AccountQuery) -> str:
    """Nur feste Werte, nie der Suchtext (kann einen Namen enthalten)."""
    f = query.filter
    parts = {
        "format": fmt,
        "zeilen": count,
        "mandant": query.tenant or "alle",
        "anzeige": "geflaggt" if f.flagged_only else "alle",
        "status": f.status or "-",
        "kontoart": f.account_type or "-",
        "lizenziert": _yes_no(f.licensed, "-"),
        "quelle": f.source or "-",
        "suche": "ja" if f.query else "nein",
    }
    return " ".join(f"{k}={v}" for k, v in parts.items())


@router.get("/export", dependencies=[Depends(require_same_origin)])
def export(request: Request) -> Response:
    """Liefert die aktuelle Filteransicht als Datei und schreibt den Export ins Audit-Log."""
    fmt = request.query_params.get("format", "")
    if fmt not in MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="Format muss csv oder xlsx sein")
    query = parse_account_query(request.query_params)
    settings: Settings = request.app.state.settings
    tz = settings.tzinfo
    now = utcnow()
    with db_session(request) as session, session.begin():
        selected = [t for t in enabled_tenants(session) if query.tenant in (None, t.slug)]
        if not selected:
            raise HTTPException(status_code=404, detail="Mandant unbekannt")
        rows: list[AccountRow] = []
        for tenant in selected:
            rows.extend(load_accounts(session, tenant, now))
        shown = apply_filter(rows, query.filter)
        info = ExportInfo(
            created_at=now,
            tenants=[
                TenantInfo(t.name, t.inactive_days, t.long_inactive_days, t.new_account_grace_days)
                for t in selected
            ],
            filters=_filter_lines(query, selected),
        )
        file_name = _file_name(selected, local_date(now, tz).isoformat(), fmt)
        session.add(
            AuditLog(
                tenant_id=selected[0].id if len(selected) == 1 else None,
                at=now,
                actor=actor_name(request),
                action=f"export.{fmt}",
                detail=_audit_detail(fmt, len(shown), query),
            )
        )
    content = build_csv(shown, tz) if fmt == "csv" else build_xlsx(shown, info, tz)
    log.info("Export erstellt format=%s zeilen=%s", fmt, len(shown))
    return Response(
        content,
        media_type=MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-store",
        },
    )
