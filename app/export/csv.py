"""CSV-Export: Semikolon, UTF-8 mit BOM, Datum ``TT.MM.JJJJ HH:MM`` (öffnet in Excel richtig)."""

from __future__ import annotations

import csv
import io
from datetime import tzinfo

from app.core.accounts import AccountRow
from app.export.columns import HEADERS, text_row

BOM = "﻿"


def build_csv(rows: list[AccountRow], tz: tzinfo) -> bytes:
    """Baut die CSV-Datei: Kopfzeile plus eine Zeile je Konto, Zeilenende ``\\r\\n``."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(text_row(row, tz))
    return (BOM + buffer.getvalue()).encode("utf-8")
