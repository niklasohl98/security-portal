from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.db.models import AuditLog
from app.export.csv import BOM
from tests.identity_fakes import seed_world
from tests.web_fakes import open_session


@pytest.fixture
def world(site: TestClient) -> TestClient:
    with open_session(site) as s, s.begin():
        seed_world(s)
    return site


def _csv_names(response_text: str) -> list[str]:
    rows = response_text.removeprefix(BOM).split("\r\n")[1:-1]
    return [r.split(";")[1] for r in rows]


def test_csv_export_downloads_the_current_view_as_an_excel_ready_file(world: TestClient) -> None:
    r = world.get("/export?format=csv&tenant=cg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        r.headers["content-disposition"]
        == 'attachment; filename="Inaktive_Konten_CG_2026-11-06.csv"'
    )
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(BOM.encode("utf-8"))
    assert _csv_names(r.text) == ["Person 2", "Person 1", "nur.ad"]  # geflaggt, wie die Liste


def test_export_uses_exactly_the_filters_of_the_list(world: TestClient) -> None:
    listed = world.get("/accounts?scope=all&licensed=no").text
    exported = _csv_names(world.get("/export?format=csv&scope=all&licensed=no").text)
    assert sorted(exported) == ["Person 3", "nur.ad"]
    assert all(name in listed for name in exported)
    assert _csv_names(world.get("/export?format=csv&q=PERSON+1").text) == ["Person 1"]
    assert _csv_names(world.get("/export?format=csv&source=ad").text) == ["nur.ad"]
    assert _csv_names(world.get("/export?format=csv&status=inactive_long").text) == ["Person 1"]


def test_export_ignores_the_page_parameter(world: TestClient) -> None:
    assert _csv_names(world.get("/export?format=csv&page=7").text) == [
        "Person 2",
        "Person 1",
        "nur.ad",
    ]


def test_xlsx_export_is_a_workbook_with_a_dated_file_name(world: TestClient) -> None:
    r = world.get("/export?format=xlsx&tenant=cg&scope=all")
    assert r.status_code == 200
    assert r.headers["content-type"].endswith("spreadsheetml.sheet")
    assert (
        r.headers["content-disposition"]
        == 'attachment; filename="Inaktive_Konten_CG_2026-11-06.xlsx"'
    )
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Konten", "Lizenzen", "Info"]
    assert wb["Konten"].max_row == 5  # Kopfzeile + 4 Konten


def test_export_without_tenant_filter_covers_all_tenants(world: TestClient) -> None:
    r = world.get("/export?format=csv")
    assert 'filename="Inaktive_Konten_Alle_2026-11-06.csv"' in r.headers["content-disposition"]
    assert _csv_names(r.text) == ["Person 2", "Person 1", "nur.ad"]


def test_every_export_is_audited_without_the_search_text(world: TestClient) -> None:
    world.get("/export?format=csv&tenant=cg&q=GeheimerName")
    world.get("/export?format=xlsx&scope=all")
    with open_session(world) as s:
        first, second = s.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        assert (first.action, first.actor) == ("export.csv", "Tester")
        assert first.tenant_id is not None  # ein Mandant
        assert second.action == "export.xlsx"
        assert second.tenant_id is None  # mehrere Mandanten
        assert "GeheimerName" not in (first.detail or "")
        assert first.detail is not None
        assert "suche=ja" in first.detail
        assert "zeilen=0" in first.detail  # kein Konto heißt so
        assert "format=xlsx" in (second.detail or "")
        assert "zeilen=4" in (second.detail or "")


def test_invalid_requests(world: TestClient) -> None:
    assert world.get("/export").status_code == 400
    assert world.get("/export?format=pdf").status_code == 400
    assert world.get("/export?format=csv&tenant=gibtsnicht").status_code == 404
    with open_session(world) as s:
        assert s.scalars(select(AuditLog)).all() == []  # abgelehnte Anfragen nicht im Log


@pytest.mark.parametrize(
    "headers",
    [{"origin": "https://boese.example"}, {"sec-fetch-site": "cross-site"}],
)
def test_cross_site_requests_cannot_trigger_an_export(
    world: TestClient, headers: dict[str, str]
) -> None:
    assert world.get("/export?format=csv", headers=headers).status_code == 403
    with open_session(world) as s:
        assert s.scalars(select(AuditLog)).all() == []


def test_same_origin_navigation_is_allowed(world: TestClient) -> None:
    ok = {"sec-fetch-site": "same-origin"}
    assert world.get("/export?format=csv", headers=ok).status_code == 200
    assert world.get("/export?format=csv", headers={"sec-fetch-site": "none"}).status_code == 200


def test_accounts_page_links_to_the_export_of_the_current_view(world: TestClient) -> None:
    text = world.get("/accounts?scope=all&status=inactive&q=abc&page=2").text
    assert 'href="/export?scope=all&amp;status=inactive&amp;q=abc&amp;format=csv"' in text
    assert 'href="/export?scope=all&amp;status=inactive&amp;q=abc&amp;format=xlsx"' in text
    assert "page=2&amp;format" not in text  # die Seite gehört nicht zum Export


def test_file_name_is_safe_for_unusual_tenant_names(world: TestClient) -> None:
    from app.db.models import Tenant

    with open_session(world) as s, s.begin():
        s.scalars(select(Tenant).where(Tenant.slug == "cg")).one().name = 'Ä/Ö "Kunde"\r\n; x'
    r = world.get("/export?format=csv&tenant=cg")
    disposition = r.headers["content-disposition"]
    assert disposition == 'attachment; filename="Inaktive_Konten_Kunde_x_2026-11-06.csv"'
    assert "\r" not in disposition
    assert "\n" not in disposition
