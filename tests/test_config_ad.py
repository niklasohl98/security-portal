"""Mandantenkonfiguration der AD-Anbindung: CA-Datei, befristete Ausnahme, DC-Namen."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ConfigError, Settings, load_tenants, resolve_ca_file
from app.core.tenants import sync_tenants
from app.db.models import Tenant
from app.db.session import create_db_engine, make_session_factory, run_migrations


@pytest.fixture
def db_session(settings: Settings) -> Iterator[Session]:
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


def _write(tmp_path: Path, **ad_extra: object) -> Path:
    ad: dict[str, object] = {"domain": "beispiel.invalid", "bind_user": "svc", "bind_secret": "b"}
    ad.update(ad_extra)
    p = tmp_path / "tenants.yaml"
    p.write_text(yaml.safe_dump({"tenants": [{"slug": "t", "name": "T", "ad": ad}]}), "utf-8")
    return p


def test_new_fields_default_to_none(tmp_path: Path) -> None:
    ad = load_tenants(_write(tmp_path))[0].ad
    assert ad is not None
    assert (ad.ca_file, ad.accept_weak_dc_keys_until) == (None, None)


def test_new_fields_are_parsed(tmp_path: Path) -> None:
    raw = "tenants:\n  - slug: t\n    name: T\n    ad:\n      domain: beispiel.invalid\n"
    raw += "      bind_user: svc\n      bind_secret: b\n      ca_file: ca/root.pem\n"
    raw += "      accept_weak_dc_keys_until: 2026-12-31\n"
    path = tmp_path / "tenants.yaml"
    path.write_text(raw, encoding="utf-8")
    ad = load_tenants(path)[0].ad
    assert ad is not None
    assert (ad.ca_file, ad.accept_weak_dc_keys_until) == ("ca/root.pem", date(2026, 12, 31))


@pytest.mark.parametrize(
    "bad",
    ["/etc/ssl/ca.pem", "../ca.pem", "ca/../../x.pem", "ca//x.pem", ".versteckt", "C:\\ca.pem", ""],
)
def test_unsafe_ca_file_is_rejected_without_echoing_the_value(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_tenants(_write(tmp_path, ca_file=bad))
    assert "ad.ca_file" in str(caught.value)
    if bad:
        assert bad not in str(caught.value)


def test_invalid_exception_date_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        load_tenants(_write(tmp_path, accept_weak_dc_keys_until="irgendwann"))
    assert "accept_weak_dc_keys_until" in str(caught.value)
    assert "irgendwann" not in str(caught.value)


def test_dc_names_are_validated(tmp_path: Path) -> None:
    ad = load_tenants(_write(tmp_path, dcs=["dc1.beispiel.invalid", "dc-2"]))[0].ad
    assert ad is not None
    assert ad.dcs == ["dc1.beispiel.invalid", "dc-2"]
    for bad in ("dc1;rm", "dc 1", "-dc", "dc1/../x", "ldap://dc1"):
        with pytest.raises(ConfigError) as caught:
            load_tenants(_write(tmp_path, dcs=[bad]))
        assert bad not in str(caught.value)


def test_resolve_ca_file(tmp_path: Path) -> None:
    (tmp_path / "ca").mkdir()
    pem = tmp_path / "ca" / "root.pem"
    pem.write_text("PEM", encoding="utf-8")
    tenants_file = tmp_path / "tenants.yaml"
    assert resolve_ca_file(tenants_file, "ca/root.pem") == pem.resolve()


def test_resolve_ca_file_rejects_missing_directory_and_symlink_escape(tmp_path: Path) -> None:
    config = tmp_path / "config"
    (config / "ca").mkdir(parents=True)
    outside = tmp_path / "geheim.pem"
    outside.write_text("PEM", encoding="utf-8")
    (config / "ca" / "link.pem").symlink_to(outside)
    tenants_file = config / "tenants.yaml"
    for given in ("ca/fehlt.pem", "ca", "ca/link.pem"):
        with pytest.raises(ConfigError):
            resolve_ca_file(tenants_file, given)


def test_tenant_sync_stores_and_clears_the_new_fields(tmp_path: Path, db_session: Session) -> None:
    with_ad = load_tenants(
        _write(tmp_path, ca_file="ca/root.pem", accept_weak_dc_keys_until=date(2026, 12, 31))
    )
    sync_tenants(db_session, with_ad)
    row = db_session.scalars(select(Tenant).where(Tenant.slug == "t")).one()
    assert (row.ad_ca_file, row.ad_weak_keys_until) == ("ca/root.pem", date(2026, 12, 31))

    sync_tenants(db_session, load_tenants(_write(tmp_path)))
    assert (row.ad_ca_file, row.ad_weak_keys_until) == (None, None)  # Ausnahme entfernt
