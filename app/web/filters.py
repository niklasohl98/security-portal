"""Filter der Kontenliste aus den Abfrageparametern (gemeinsam für Liste und Export).

Unbekannte oder ungültige Werte werden ignoriert (nie als Fehler zurückgemeldet und nie
reflektiert); ein ungültiger Filter blendet also nichts aus, was der Nutzer nicht sah.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

from app.core.accounts import SOURCES, AccountFilter
from app.web.labels import ACCOUNT_TYPE_LABELS, STATUS_LABELS

MAX_QUERY_LENGTH = 100
_STATUSES = tuple(s for s in STATUS_LABELS if s != "missing")


@dataclass(frozen=True)
class AccountQuery:
    """Geparste Abfrage: Mandant (``None`` = alle), Filter, Seite."""

    tenant: str | None
    filter: AccountFilter
    page: int = 1

    def params(self, **override: str | int | None) -> dict[str, str]:
        """Parameter für Links (Seitenwechsel, Export); nur gesetzte Filter."""
        f = self.filter
        raw: dict[str, str | int | None] = {
            "tenant": self.tenant,
            "scope": "flagged" if f.flagged_only else "all",
            "status": f.status,
            "type": f.account_type,
            "licensed": None if f.licensed is None else ("yes" if f.licensed else "no"),
            "source": f.source,
            "q": f.query or None,
            "page": self.page if self.page > 1 else None,
        }
        raw.update(override)
        return {k: str(v) for k, v in raw.items() if v not in (None, "")}

    def query_string(self, **override: str | int | None) -> str:
        """Dieselben Parameter als URL-Abfragetext."""
        return urlencode(self.params(**override))


def parse_account_query(params: Mapping[str, str]) -> AccountQuery:
    """Liest Mandant, Filter und Seite aus den Abfrageparametern."""
    licensed = params.get("licensed")
    status = params.get("status")
    kind = params.get("type")
    source = params.get("source")
    text = (params.get("q") or "").strip()[:MAX_QUERY_LENGTH]
    try:
        page = max(1, int(params.get("page", "1")))
    except ValueError:
        page = 1
    flt = AccountFilter(
        flagged_only=params.get("scope", "flagged") != "all",
        status=status if status in _STATUSES else None,
        account_type=kind if kind in ACCOUNT_TYPE_LABELS else None,
        licensed={"yes": True, "no": False}.get(licensed or ""),
        source=source if source in SOURCES else None,
        query=text or None,
    )
    return AccountQuery(tenant=params.get("tenant") or None, filter=flt, page=page)
