from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.auth import service
from app.config import Settings
from app.core.backup import create_backup, restore_backup
from app.db.models import AppSession, AppUser
from app.db.session import create_db_engine, make_session_factory, run_migrations
from tests.identity_fakes import NOW


def test_restore_preserves_users_but_never_resurrects_old_login_cookies(
    settings: Settings,
    tmp_path: Path,
) -> None:
    run_migrations(settings.database_url)
    engine = create_db_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        uid = service.create_user(
            session, "Recovered", "Ein-sicheres-Test-Passwort-7", "test", NOW, is_admin=True
        )
        login = service.create_session(session, settings, uid, NOW)
    made = create_backup(settings.database_url, settings.backup_dir, NOW)
    with factory.begin() as session:
        service.destroy_session(session, login.token)
    engine.dispose()
    restored_url = f"sqlite:///{tmp_path / 'recovered.db'}"
    restore_backup(settings.backup_dir / made.name, restored_url, NOW)
    engine = create_db_engine(restored_url)
    with make_session_factory(engine)() as session:
        assert service.load_session(session, login.token, NOW) is None
        assert not list(session.scalars(select(AppSession)))
        user = session.scalars(select(AppUser).where(AppUser.id == uid)).one()
        assert user.is_admin
        assert user.must_change_password
    engine.dispose()
