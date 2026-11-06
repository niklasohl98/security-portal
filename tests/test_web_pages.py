from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import cycle
from app.db.models import Identity, RunStatus, Source
from tests.identity_fakes import ago, get_tenant, make_run, seed_world
from tests.web_fakes import identity_id, open_session

XSS = "<img src=x onerror=alert(1)>"


@pytest.fixture
def world(site: TestClient) -> TestClient:
    with open_session(site) as s, s.begin():
        seed_world(s)
    return site


def test_navigation_and_timezone_footer_are_on_every_page(world: TestClient) -> None:
    for path in ("/", "/accounts", "/exceptions", "/runs", "/review"):
        text = world.get(path).text
        assert 'href="/accounts"' in text
        assert 'href="/review"' in text
        assert "Zeiten in Europe/Berlin" in text


def test_overview_shows_counts_licenses_runs_and_links(world: TestClient) -> None:
    r = world.get("/")
    assert r.status_code == 200
    text = r.text
    assert "<h2>CG</h2>" in text
    assert "Microsoft 365 E3" in text  # freiwerdende Lizenz
    assert "NEU_X" in text  # unbekannte SKU mit Teilenummer
    assert "Flow Free" not in text  # kostenlos
    assert 'href="/accounts?tenant=cg"' in text
    assert text.count("Keine geflaggten") == 1  # nur CVH hat keine
    assert "<h2>CVH</h2>" in text
    assert "Noch kein Abruf" in text  # CVH hat keine Läufe


def test_overview_translates_warnings_and_flags_failed_runs(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        make_run(s, get_tenant(s, "cvh"), Source.AD, RunStatus.PARTIAL, ["ad_dc_defective"])
        make_run(s, get_tenant(s, "cvh"), Source.ENTRA, RunStatus.FAILED)
    text = world.get("/").text
    assert "Ein Domänencontroller ist defekt" in text
    assert "Der jüngste Abruf ist fehlgeschlagen" in text
    assert "ad_dc_defective" not in text  # Code erscheint nicht roh


def test_accounts_default_lists_flagged_sorted_never_first(world: TestClient) -> None:
    text = world.get("/accounts").text
    assert text.index("Person 2") < text.index("Person 1") < text.index("nur.ad")
    assert "Person 3" not in text  # aktiv, nicht geflaggt
    assert "3 Konten" in text
    assert "Microsoft 365 E3" in text
    assert "Flow Free" not in text  # nur bezahlte Lizenzen in der Liste


def test_accounts_filters(world: TestClient) -> None:
    assert "Person 3" in world.get("/accounts?scope=all").text
    only_ad = world.get("/accounts?source=ad").text
    assert "nur.ad" in only_ad
    assert "Person 1" not in only_ad
    assert "Person 1" in world.get("/accounts?status=inactive_long").text
    assert "Person 2" not in world.get("/accounts?status=inactive_long").text
    licensed = world.get("/accounts?licensed=no").text
    assert "nur.ad" in licensed
    assert "Person 2" not in licensed
    assert "Person 1" in world.get("/accounts?q=PERSON+1").text
    assert "Person 2" not in world.get("/accounts?q=PERSON+1").text
    assert "0 Konten" in world.get("/accounts?tenant=cvh").text


def test_accounts_keep_selected_filters_in_the_form(world: TestClient) -> None:
    text = world.get("/accounts?scope=all&status=inactive&licensed=yes&q=abc").text
    assert '<option value="all" selected>' in text
    assert '<option value="inactive" selected>' in text
    assert '<option value="yes" selected>' in text
    assert 'value="abc"' in text


def test_accounts_ignore_invalid_params_and_never_reflect_them(world: TestClient) -> None:
    r = world.get(
        f"/accounts?status={XSS}&type=x&licensed=vielleicht&source=y&page=abc&tenant={XSS}"
    )
    assert r.status_code == 200
    assert XSS not in r.text
    assert "&lt;img" not in r.text  # auch nicht maskiert zurückgegeben
    assert "0 Konten" in r.text  # unbekannter Mandant: nichts angezeigt


def test_account_names_are_escaped(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        s.add(
            Identity(
                tenant_id=get_tenant(s).id,
                key="x",
                display_name=XSS,
                status="never",
                flagged=True,
                account_type="member_cloud",
            )
        )
    text = world.get("/accounts").text
    assert XSS not in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text


def test_accounts_paginate(world: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.pages.PAGE_SIZE", 2)
    first = world.get("/accounts").text
    assert "Seite 1 von 2" in first
    assert "Person 2" in first
    assert "nur.ad" not in first
    assert 'href="/accounts?scope=flagged&amp;page=2"' in first or "page=2" in first
    second = world.get("/accounts?page=2").text
    assert "nur.ad" in second
    assert "Person 2" not in second
    assert "Seite 2 von 2" in second
    assert "Seite 2 von 2" in world.get("/accounts?page=99").text  # zu große Seite wird begrenzt


def test_account_detail_shows_both_sources_side_by_side(world: TestClient) -> None:
    text = world.get(f"/accounts/cg/{identity_id(world, 'Person 1')}").text
    assert "Person 1" in text
    assert "Microsoft 365 E3" in text
    assert "kostenlos" in text  # die freie Lizenz ist gekennzeichnet
    assert "Lizenz entziehen, dann deaktivieren" in text
    assert "Kein AD-Konto" in text
    assert "vor 200 Tagen" in text
    ad = world.get(f"/accounts/cg/{identity_id(world, 'nur.ad')}").text
    assert "Kein Entra-Konto" in ad
    assert "dc1.beispiel.test" in ad
    assert "dc2.beispiel.test" in ad  # auch ein DC ohne Wert
    assert "OU=Test,DC=beispiel,DC=test" in ad
    assert "Deaktiviert</dt><dd>ja" in ad


def test_account_detail_is_scoped_to_the_tenant(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        foreign = Identity(tenant_id=get_tenant(s, "cvh").id, key="x", display_name="Fremd")
        s.add(foreign)
        s.flush()
        foreign_id = foreign.id
        s.add(
            Identity(
                tenant_id=get_tenant(s).id, key="weg", display_name="Weg", missing_since=ago(1)
            )
        )
        s.flush()
    assert (
        world.get(f"/accounts/cg/{foreign_id}").status_code == 404
    )  # richtige ID, falscher Mandant
    assert world.get(f"/accounts/cg/{identity_id(world, 'Weg')}").status_code == 404  # verschwunden
    assert world.get("/accounts/gibtsnicht/1").status_code == 404
    assert world.get("/accounts/cg/999999").status_code == 404


def test_detail_messages_come_from_fixed_lists(world: TestClient) -> None:
    path = f"/accounts/cg/{identity_id(world, 'Person 1')}"
    assert "Bitte einen Grund angeben." in world.get(f"{path}?err=reason_required").text
    assert "Ausnahme angelegt." in world.get(f"{path}?done=exception").text
    text = world.get(f"{path}?err={XSS}&done={XSS}").text
    assert XSS not in text
    assert 'role="alert"' not in text
    assert 'role="status"' not in text


def test_runs_page_lists_runs_with_translated_status(world: TestClient) -> None:
    text = world.get("/runs").text
    assert "erfolgreich" in text
    assert "Jetzt abrufen: CG" in text
    assert "Jetzt abrufen: CVH" in text
    assert "Entra" in text
    assert "AD" in text


def test_runs_page_disables_the_button_while_a_cycle_runs(world: TestClient) -> None:
    lock = cycle._lock("cg")
    assert lock.acquire(blocking=False)
    try:
        text = world.get("/runs").text
    finally:
        lock.release()
    assert "Jetzt abrufen: CG (läuft)" in text
    assert "disabled" in text


def test_review_page_still_renders_local_time(world: TestClient) -> None:
    with open_session(world) as s, s.begin():
        s.add(
            Identity(
                tenant_id=get_tenant(s).id,
                key="weg",
                display_name="Weg",
                missing_since=ago(0),  # 06.11.2026 12:00 UTC = 13:00 Berlin
            )
        )
    assert "06.11.2026 13:00" in world.get("/review").text
