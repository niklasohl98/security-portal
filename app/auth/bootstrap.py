"""Initialkonto: gibt es noch keinen Benutzer, entsteht ``Admin`` mit einem bekannten Startpasswort.

Das Startpasswort steht absichtlich im Code (ausdrücklicher Wunsch, ADR 0007), ist also **nicht
geheim**. Deshalb gilt: Das Konto hat ``must_change_password``; solange es besteht, erreicht dieser
Benutzer nur die Seite zum Passwortwechsel. Das Startpasswort ist als neues Passwort verboten. Das
Konto wird nur angelegt, wenn die Tabelle leer ist (nie erneut, auch nicht nach einem Wechsel).
"""

from __future__ import annotations

INITIAL_USERNAME = "Admin"
INITIAL_PASSWORD = "Passwort123"  # noqa: S105 - bekanntes Startpasswort, Wechsel erzwungen (ADR 0007)
