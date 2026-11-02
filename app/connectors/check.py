"""Verbindungstest für einen Entra-Mandanten (Token, Organisation, SKUs, signInActivity)."""

from __future__ import annotations

from dataclasses import dataclass

from app.connectors.graph import (
    GRAPH_BASE,
    GraphClient,
    GraphError,
    Organization,
    fetch_organization,
    fetch_skus,
)


@dataclass(frozen=True)
class EntraCheck:
    """Ergebnis des Verbindungstests."""

    organization: Organization
    sku_count: int
    sign_in_available: bool


def check_entra(client: GraphClient) -> EntraCheck:
    """Prüft Token und Berechtigungen mit drei kleinen Abfragen.

    Raises:
        GraphError: Token oder Berechtigung fehlt (nicht ``signInActivity``: das ergibt nur
            ``sign_in_available=False``).
    """
    org = fetch_organization(client)
    skus = fetch_skus(client)
    try:
        client.get(f"{GRAPH_BASE}/users", {"$select": "id,signInActivity", "$top": "1"})
        sign_in = True
    except GraphError as exc:
        if exc.status != 403:
            raise
        sign_in = False
    return EntraCheck(org, len(skus), sign_in)
