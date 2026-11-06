"""Hilfen für Tests der Oberfläche."""

from __future__ import annotations

from typing import cast

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Identity
from tests.identity_fakes import get_tenant


def open_session(client: TestClient) -> Session:
    """Neue Datenbanksitzung der Anwendung im TestClient."""
    session: Session = cast(FastAPI, client.app).state.session_factory()
    return session


def post_form(
    client: TestClient,
    path: str,
    headers: dict[str, str] | None = None,
    *,
    csrf: bool = True,
    **fields: str,
) -> httpx.Response:
    """Schickt ein Formular, ohne Weiterleitungen zu folgen.

    Bei einem angemeldeten Client (``tests.conftest.AuthedClient``) hängt es das CSRF-Token der
    Sitzung an, außer mit ``csrf=False``.
    """
    token = getattr(client, "csrf_token", "")
    if csrf and token:
        fields = {**fields, "csrf_token": token}
    body = "&".join(f"{k}={v}" for k, v in fields.items())
    response: httpx.Response = client.post(
        path,
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded", **(headers or {})},
        follow_redirects=False,
    )
    return response


def identity_id(client: TestClient, name: str, slug: str = "cg") -> int:
    """ID der Identität mit diesem Anzeigenamen."""
    with open_session(client) as s:
        row = s.scalars(
            select(Identity).where(
                Identity.tenant_id == get_tenant(s, slug).id, Identity.display_name == name
            )
        ).one()
        return row.id
