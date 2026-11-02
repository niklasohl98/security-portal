from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import ConfigError, Settings, load_tenants, read_secret
from tests.conftest import EXAMPLE_TENANTS

GUID_A = "00000000-0000-4000-8000-0000000000aa"
GUID_B = "00000000-0000-4000-8000-0000000000bb"
THUMB = "0123456789abcdef0123456789abcdef01234567"


def _tenant(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "slug": "t1",
        "name": "T1",
        "entra": {
            "tenant_id": GUID_A,
            "client_id": GUID_B,
            "certificate_secret": "t1_cert",
            "certificate_thumbprint": THUMB,
        },
    }
    base.update(over)
    return base


def _write(tmp_path: Path, tenants: list[dict[str, object]]) -> Path:
    p = tmp_path / "tenants.yaml"
    p.write_text(yaml.safe_dump({"tenants": tenants}), encoding="utf-8")
    return p


def test_example_file_is_valid() -> None:
    tenants = load_tenants(EXAMPLE_TENANTS)
    assert [t.slug for t in tenants] == ["cg", "cvh"]
    cg, cvh = tenants
    assert cg.thresholds.inactive_days == 30
    assert cvh.thresholds.long_inactive_days == 90  # Standardwert bei weggelassenem Block
    assert cg.entra is not None
    assert cg.entra.certificate_thumbprint == cg.entra.certificate_thumbprint.upper()


def test_entra_only_and_ad_only_are_allowed(tmp_path: Path) -> None:
    ad_only = _tenant(
        slug="a", entra=None, ad={"domain": "a.invalid", "bind_user": "u", "bind_secret": "s"}
    )
    assert len(load_tenants(_write(tmp_path, [_tenant(), ad_only]))) == 2


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"entra": None}, "mindestens"),
        ({"slug": "Bad Slug"}, "slug"),
        ({"unknown_key": 1}, "unknown_key"),
        ({"thresholds": {"inactive_days": 90, "long_inactive_days": 30}}, "long_inactive_days"),
        ({"thresholds": {"inactive_days": 0}}, "inactive_days"),
    ],
)
def test_invalid_tenant_is_rejected(
    tmp_path: Path, override: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ConfigError, match=fragment):
        load_tenants(_write(tmp_path, [_tenant(**override)]))


@pytest.mark.parametrize(
    "entra_patch",
    [
        {"tenant_id": "keine-guid"},
        {"certificate_thumbprint": "xyz"},
        {"certificate_secret": "../etc/passwd"},
    ],
)
def test_invalid_entra_fields_are_rejected(tmp_path: Path, entra_patch: dict[str, str]) -> None:
    t = _tenant()
    t["entra"] = {**t["entra"], **entra_patch}  # type: ignore[dict-item]
    with pytest.raises(ConfigError):
        load_tenants(_write(tmp_path, [t]))


def test_error_message_does_not_echo_input_values(tmp_path: Path) -> None:
    t = _tenant()
    t["entra"] = {**t["entra"], "tenant_id": "GEHEIMER-WERT-123"}  # type: ignore[dict-item]
    with pytest.raises(ConfigError) as exc:
        load_tenants(_write(tmp_path, [t]))
    assert "GEHEIMER-WERT-123" not in str(exc.value)


def test_duplicate_slugs_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="eindeutig"):
        load_tenants(_write(tmp_path, [_tenant(), _tenant()]))


def test_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nicht lesbar"):
        load_tenants(tmp_path / "gibt-es-nicht.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("tenants: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_tenants(bad)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("- eine\n- liste\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Mapping"):
        load_tenants(scalar)


def test_read_secret(tmp_path: Path) -> None:
    (tmp_path / "pw").write_text("s3cret\n", encoding="utf-8")
    assert read_secret("pw", tmp_path) == "s3cret"
    with pytest.raises(ConfigError) as exc:
        read_secret("fehlt", tmp_path)
    assert "s3cret" not in str(exc.value)


@pytest.mark.parametrize("name", ["../pw", "a/b", "", ".hidden", "a b"])
def test_read_secret_rejects_path_tricks(tmp_path: Path, name: str) -> None:
    with pytest.raises(ConfigError, match="Ungültig"):
        read_secret(name, tmp_path)


def test_settings_from_env_defaults_and_overrides() -> None:
    s = Settings.from_env({})
    assert s.data_dir == Path("/data")
    assert s.database_url == "sqlite:////data/kontenwaechter.db"
    assert s.secrets_dir == Path("/run/secrets")
    s2 = Settings.from_env(
        {"KW_DATA_DIR": "/x", "KW_LOG_LEVEL": "debug", "KW_DATABASE_URL": "sqlite://"}
    )
    assert (s2.data_dir, s2.log_level, s2.database_url) == (Path("/x"), "DEBUG", "sqlite://")
