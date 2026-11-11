from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core import backup
from app.core.backup import (
    PRE_MIGRATION_PREFIX,
    BackupError,
    backup_before_migration,
    backup_status,
    create_backup,
    list_backups,
    migration_pending,
    prune_backups,
    restore_backup,
    run_backup,
    sqlite_path,
    verify_backup,
)
from app.db.models import AuditLog, Identity
from app.db.session import create_db_engine, make_session_factory, migration_head, run_migrations
from tests.identity_fakes import NOW, get_tenant


def _utc(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _age(path: Path, days: float, now: datetime = NOW) -> None:
    ts = (now - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


# --- Sicherung ---


def test_a_backup_contains_the_data_and_passes_the_check(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        s.add(Identity(tenant_id=get_tenant(s).id, key="k", display_name="Anna"))
    made = create_backup(settings.database_url, settings.backup_dir, _utc(2026, 11, 6, 1, 30, 5))
    assert made.name == "kontenwaechter-20261106-013005.db"
    assert made.size > 0
    assert made.pre_migration is False
    verify_backup(settings.backup_dir / made.name)
    with sqlite3.connect(settings.backup_dir / made.name) as conn:
        assert conn.execute("SELECT display_name FROM identity").fetchall() == [("Anna",)]


def test_a_backup_includes_changes_that_are_still_only_in_the_wal(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    """Die Anwendung läuft im WAL-Modus: frische Änderungen stehen zunächst nur im WAL."""
    with factory.begin() as s:
        s.add(Identity(tenant_id=get_tenant(s).id, key="nur-im-wal", display_name="Neu"))
    made = create_backup(settings.database_url, settings.backup_dir, NOW)  # Verbindungen noch offen
    with sqlite3.connect(settings.backup_dir / made.name) as conn:
        assert conn.execute("SELECT key FROM identity").fetchall() == [("nur-im-wal",)]
    assert [p.name for p in settings.backup_dir.iterdir()] == [
        made.name
    ]  # eine Datei, kein -wal/-shm


def test_backup_files_are_private_and_no_temporary_file_stays(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    made = create_backup(settings.database_url, settings.backup_dir, NOW)
    path = settings.backup_dir / made.name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.backup_dir.stat().st_mode) == 0o700  # vom Programm angelegt
    assert [p.name for p in settings.backup_dir.iterdir()] == [made.name]


def test_pre_migration_backups_get_their_own_prefix(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    made = create_backup(settings.database_url, settings.backup_dir, NOW, PRE_MIGRATION_PREFIX)
    assert made.name.startswith("vor-migration-")
    assert made.pre_migration is True


def test_failures_leave_nothing_behind_and_carry_a_fixed_code(
    factory: sessionmaker[Session], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: Path) -> None:
        raise BackupError("verify")

    monkeypatch.setattr(backup, "verify_backup", broken)
    with pytest.raises(BackupError) as err:
        create_backup(settings.database_url, settings.backup_dir, NOW)
    assert err.value.code == "verify"
    assert list(settings.backup_dir.iterdir()) == []  # auch keine .tmp-Datei


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("postgresql://u:p@h/db", "not_sqlite"),
        ("sqlite://", "not_sqlite"),
        ("kaputt", "not_sqlite"),
    ],
)
def test_other_databases_are_not_supported(tmp_path: Path, url: str, code: str) -> None:
    with pytest.raises(BackupError) as err:
        create_backup(url, tmp_path, NOW)
    assert err.value.code == code


def test_missing_database_and_unusable_directory(
    factory: sessionmaker[Session], settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(BackupError) as err:
        create_backup(f"sqlite:///{tmp_path / 'gibtsnicht.db'}", tmp_path / "b", NOW)
    assert err.value.code == "no_db"
    blocker = tmp_path / "datei"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(BackupError) as err:
        create_backup(settings.database_url, blocker / "unter", NOW)  # Elternteil ist eine Datei
    assert err.value.code == "dir"


def test_sqlite_path_reads_absolute_and_relative_urls() -> None:
    assert sqlite_path("sqlite:////data/x.db") == Path("/data/x.db")
    assert sqlite_path("sqlite:///rel.db") == Path("rel.db")
    assert sqlite_path("sqlite:///:memory:") is None


# --- Prüfung ---


def test_verify_rejects_missing_garbage_and_foreign_databases(tmp_path: Path) -> None:
    with pytest.raises(BackupError) as err:
        verify_backup(tmp_path / "fehlt.db")
    assert err.value.code == "no_file"
    garbage = tmp_path / "muell.db"
    garbage.write_bytes(b"das ist keine datenbank" * 100)
    with pytest.raises(BackupError) as err:
        verify_backup(garbage)
    assert err.value.code == "verify"
    foreign = tmp_path / "fremd.db"
    with sqlite3.connect(foreign) as conn:
        conn.execute("CREATE TABLE etwas (x)")
    with pytest.raises(BackupError) as err:
        verify_backup(foreign)  # gültig, aber nicht unsere Tabellen
    assert err.value.code == "verify"


def test_verify_detects_a_truncated_backup(
    factory: sessionmaker[Session], settings: Settings, tmp_path: Path
) -> None:
    made = create_backup(settings.database_url, settings.backup_dir, NOW)
    data = (settings.backup_dir / made.name).read_bytes()
    cut = tmp_path / "abgeschnitten.db"
    cut.write_bytes(data[: len(data) // 2])
    with pytest.raises(BackupError):
        verify_backup(cut)


# --- Auflisten und Aufbewahren ---


def test_listing_shows_only_our_files_newest_first(tmp_path: Path) -> None:
    for name, days in (
        ("kontenwaechter-20261101-000000.db", 5),
        ("kontenwaechter-20261105-000000.db", 1),
        ("vor-migration-20261103-000000.db", 3),
        ("fremd.db", 0),
        ("kontenwaechter-20261106-000000.db.tmp", 0),
        ("notizen.txt", 0),
    ):
        (tmp_path / name).write_bytes(b"x")
        _age(tmp_path / name, days)
    (tmp_path / "unterordner.db").mkdir()
    got = list_backups(tmp_path)
    assert [b.name for b in got] == [
        "kontenwaechter-20261105-000000.db",
        "vor-migration-20261103-000000.db",
        "kontenwaechter-20261101-000000.db",
    ]
    assert [b.pre_migration for b in got] == [False, True, False]
    assert list_backups(tmp_path / "gibtsnicht") == []


def test_pruning_deletes_old_files_but_always_keeps_the_newest(tmp_path: Path) -> None:
    for name, days in (("a", 30), ("b", 20), ("c", 10), ("d", 2)):
        path = tmp_path / f"kontenwaechter-{name}.db"
        path.write_bytes(b"x")
        _age(path, days)
    other = tmp_path / "wichtig.txt"
    other.write_text("bleibt", encoding="utf-8")
    _age(other, 400)
    assert prune_backups(tmp_path, 14, NOW) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "kontenwaechter-c.db",
        "kontenwaechter-d.db",
        "wichtig.txt",
    ]


def test_the_newest_backup_survives_even_when_everything_is_old(tmp_path: Path) -> None:
    for name, days in (("a", 100), ("b", 50)):
        path = tmp_path / f"kontenwaechter-{name}.db"
        path.write_bytes(b"x")
        _age(path, days)
    assert prune_backups(tmp_path, 14, NOW) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["kontenwaechter-b.db"]


def test_keep_days_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mindestens 1"):
        prune_backups(tmp_path, 0, NOW)


# --- Wiederherstellung ---


def test_restore_brings_the_data_back_and_keeps_the_old_file(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory.begin() as s:
        s.add(Identity(tenant_id=get_tenant(s).id, key="alt", display_name="Vor dem Unfall"))
    made = create_backup(settings.database_url, settings.backup_dir, NOW)
    with factory.begin() as s:  # der „Unfall“: Daten ändern sich
        for row in s.scalars(select(Identity)):
            s.delete(row)
        s.add(Identity(tenant_id=get_tenant(s).id, key="neu", display_name="Nach dem Unfall"))
    factory.kw["bind"].dispose()  # die Anwendung ist gestoppt

    aside = restore_backup(
        settings.backup_dir / made.name, settings.database_url, _utc(2026, 11, 6, 15, 0, 0)
    )

    assert aside.name.endswith(".vor-restore-20261106-150000")
    engine = create_db_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as s:
            assert [i.display_name for i in s.scalars(select(Identity))] == ["Vor dem Unfall"]
    finally:
        engine.dispose()
    with sqlite3.connect(aside) as conn:  # die bisherige Datei ist erhalten
        assert conn.execute("SELECT display_name FROM identity").fetchall() == [
            ("Nach dem Unfall",)
        ]


def test_restore_removes_stale_wal_files_and_works_without_an_old_database(
    settings: Settings, tmp_path: Path
) -> None:
    run_migrations(settings.database_url)
    made = create_backup(settings.database_url, settings.backup_dir, NOW)
    target = sqlite_path(settings.database_url)
    assert target is not None
    Path(str(target) + "-wal").write_bytes(b"veraltet")
    Path(str(target) + "-shm").write_bytes(b"veraltet")
    restore_backup(settings.backup_dir / made.name, settings.database_url, NOW)
    assert not Path(str(target) + "-wal").exists()
    assert not Path(str(target) + "-shm").exists()
    empty_url = f"sqlite:///{tmp_path / 'neu' / 'x.db'}"  # Verzeichnis und Datei gibt es noch nicht
    result = restore_backup(settings.backup_dir / made.name, empty_url, NOW)
    assert result.name == "x.db"
    verify_backup(result)


def test_restore_refuses_bad_input_and_leaves_the_database_untouched(
    settings: Settings, tmp_path: Path
) -> None:
    run_migrations(settings.database_url)
    target = sqlite_path(settings.database_url)
    assert target is not None
    before = target.read_bytes()
    garbage = tmp_path / "muell.db"
    garbage.write_bytes(b"x" * 5000)
    for source, code in ((garbage, "verify"), (tmp_path / "fehlt.db", "no_file")):
        with pytest.raises(BackupError) as err:
            restore_backup(source, settings.database_url, NOW)
        assert err.value.code == code
    with pytest.raises(BackupError) as err:
        restore_backup(garbage, "postgresql://x/y", NOW)
    assert err.value.code == "not_sqlite"
    assert target.read_bytes() == before
    assert not list(target.parent.glob("*.vor-restore-*"))


# --- vor einer Migration ---


def test_a_backup_is_made_before_a_pending_migration(settings: Settings) -> None:
    run_migrations(settings.database_url)
    target = sqlite_path(settings.database_url)
    assert target is not None
    head = migration_head()
    assert migration_pending(settings.database_url, head) is False
    with sqlite3.connect(target) as conn:  # so sieht eine Datenbank einer älteren Version aus
        conn.execute("UPDATE alembic_version SET version_num = '0001'")
    assert migration_pending(settings.database_url, head) is True
    made = backup_before_migration(settings, head)
    assert made is not None
    assert made.name.startswith("vor-migration-")
    verify_backup(settings.backup_dir / made.name)


def test_no_backup_for_a_fresh_or_current_database(settings: Settings) -> None:
    head = migration_head()
    assert backup_before_migration(settings, head) is None  # Datei gibt es noch nicht
    run_migrations(settings.database_url)
    assert backup_before_migration(settings, head) is None  # schon auf dem neuesten Stand
    assert not settings.backup_dir.exists()


def test_a_failing_pre_migration_backup_does_not_block_the_start(
    settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run_migrations(settings.database_url)
    target = sqlite_path(settings.database_url)
    assert target is not None
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '0001'")
    blocker = tmp_path / "datei"
    blocker.write_text("x", encoding="utf-8")
    broken = Settings(**{**settings.__dict__, "backup_dir": blocker / "unter"})
    assert backup_before_migration(broken, migration_head()) is None
    assert "Sicherung vor der Migration fehlgeschlagen code=dir" in caplog.text


def test_the_app_backs_up_before_migrating_on_start(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    run_migrations(settings.database_url)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parent.parent / "app/db/migrations")
    )
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(config, "0005")
    with TestClient(create_app(settings)):
        pass
    assert [b.pre_migration for b in list_backups(settings.backup_dir)] == [True]


# --- Lauf, Protokoll und Zustand ---


def test_run_backup_writes_an_audit_entry_and_prunes(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    settings.backup_dir.mkdir()
    old = settings.backup_dir / "kontenwaechter-20200101-000000.db"
    old.write_bytes(b"x")
    keep = settings.backup_dir / "kontenwaechter-20200102-000000.db"
    keep.write_bytes(b"x")
    _age(old, 400, datetime.now(UTC))
    _age(keep, 300, datetime.now(UTC))
    made = run_backup(factory, settings)
    assert made is not None
    with factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "backup.ok")).one()
        assert (entry.actor, entry.tenant_id) == ("system", None)
        assert entry.detail == f"datei={made.name} groesse={made.size} geloescht=2"
    assert [b.name for b in list_backups(settings.backup_dir)] == [made.name]


def test_run_backup_reports_failures_without_raising(
    factory: sessionmaker[Session], settings: Settings, tmp_path: Path
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x", encoding="utf-8")
    broken = Settings(**{**settings.__dict__, "backup_dir": blocker / "unter"})
    assert run_backup(factory, broken, actor="cli") is None
    with factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "backup.failed")).one()
        assert (entry.actor, entry.detail) == ("cli", "code=dir")


def test_backup_state_never_ok_stale_failed_unsupported(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    with factory() as s:
        assert backup_status(s, settings, NOW).state == "never"
    settings.backup_dir.mkdir()
    fresh = settings.backup_dir / "kontenwaechter-20261106-000000.db"
    fresh.write_bytes(b"x")
    _age(fresh, 0.5)
    with factory() as s:
        status = backup_status(s, settings, NOW)
        assert (status.state, status.last_ok_name, status.last_size) == ("ok", fresh.name, 1)
    _age(fresh, 2)  # 48 Stunden, Schwelle 36
    with factory() as s:
        assert backup_status(s, settings, NOW).state == "stale"
    _age(fresh, 0.5)
    with factory.begin() as s:
        s.add(
            AuditLog(
                tenant_id=None, at=NOW, actor="system", action="backup.failed", detail="code=io"
            )
        )
    with factory() as s:
        assert (
            backup_status(s, settings, NOW).state == "failed"
        )  # Fehlschlag neuer als die letzte Sicherung
    _age(fresh, 0)
    fresh_ts = NOW + timedelta(hours=1)
    os.utime(fresh, (fresh_ts.timestamp(), fresh_ts.timestamp()))
    with factory() as s:
        assert (
            backup_status(s, settings, NOW + timedelta(hours=2)).state == "ok"
        )  # danach wieder gelungen
    unsupported = Settings(**{**settings.__dict__, "database_url": "postgresql://u:p@h/db"})
    with factory() as s:
        assert backup_status(s, unsupported, NOW).state == "unsupported"


def test_a_pre_migration_backup_does_not_count_as_the_nightly_one(
    factory: sessionmaker[Session], settings: Settings
) -> None:
    settings.backup_dir.mkdir()
    only = settings.backup_dir / "vor-migration-20261106-000000.db"
    only.write_bytes(b"x")
    _age(only, 0.1)
    with factory() as s:
        assert backup_status(s, settings, NOW).state == "never"
