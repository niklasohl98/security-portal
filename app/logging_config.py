"""JSON-Logging auf stdout. Logs enthalten nur feste Texte mit Platzhaltern, keine Geheimnisse."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Formatiert Log-Einträge als eine JSON-Zeile."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            # Nur der Fehlertyp; Meldungen von Bibliotheken könnten Werte enthalten.
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Richtet das Root-Logging als JSON auf stdout ein (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # MSAL kann bei Protokollfehlern Token-Claims in Bibliotheksmeldungen aufnehmen.
    logging.getLogger("msal").setLevel(logging.CRITICAL)
    # Uvicorn bringt eigene Handler mit; auf denselben JSON-Handler umlenken.
    # Der Access-Log ist aus (--no-access-log), weil Pfade später Konto-IDs enthalten.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = [handler]
        lg.propagate = False
