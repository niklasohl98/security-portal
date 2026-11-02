"""Hilfen für Graph-Tests: erfundene Antworten und ein Token-Provider ohne Netz."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://graph.microsoft.com/v1.0"


def load(name: str) -> dict[str, Any]:
    """Lädt eine Fixture-Datei aus ``tests/fixtures``."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


class FakeTokens:
    """Token-Quelle, die nie das Netz anfasst."""

    def __init__(self) -> None:
        self.calls = 0

    def get_token(self) -> str:
        self.calls += 1
        return "test-token"


def make_users(n: int) -> list[dict[str, Any]]:
    """Erzeugt ``n`` erfundene Konten (z. B. für den 651-Konten-Test)."""
    return [
        {
            "id": f"00000000-0000-4000-8000-{i:012d}",
            "displayName": f"Testperson {i}",
            "userPrincipalName": f"user{i}@cg.example.invalid",
            "userType": "Member",
            "accountEnabled": True,
            "assignedLicenses": [],
            "licenseAssignmentStates": [],
            "signInActivity": {},
        }
        for i in range(n)
    ]
