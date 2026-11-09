"""FastAPI-Anwendung. Start: ``uvicorn --factory app.main:create_app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.auth.middleware import add_auth
from app.auth.service import ensure_initial_admin
from app.auth.sso_runtime import SsoRuntime
from app.config import Settings, load_tenants
from app.core.backup import backup_before_migration
from app.core.scheduler import create_scheduler
from app.core.sku_names import load_sku_names, sync_sku_names
from app.core.sync import fail_stale_runs
from app.core.tenants import sync_tenants
from app.db.session import create_db_engine, make_session_factory, migration_head, run_migrations
from app.logging_config import configure_logging
from app.web import common as web_common
from app.web import labels as web_labels
from app.web import review as web_review
from app.web.auth_pages import router as auth_router
from app.web.deployment import add_deployment_guard
from app.web.exceptions_page import router as exceptions_router
from app.web.export import router as export_router
from app.web.pages import router as pages_router
from app.web.reports_page import router as reports_router
from app.web.review import router as review_router
from app.web.runs import router as runs_router
from app.web.security import add_security_headers
from app.web.sso_pages import router as sso_router
from app.web.users_page import router as users_router

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Erzeugt die Anwendung.

    Beim Start: Logging einrichten, Migrationen ausführen, Mandanten und SKU-Namen aus den
    YAML-Dateien abgleichen, unterbrochene Läufe abschließen.
    Ein Fehler in der Konfiguration bricht den Start ab (kein stilles Weiterlaufen).

    Args:
        settings: Einstellungen; Standard sind die Umgebungsvariablen ``KW_*``.
    """
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(cfg.log_level)
        tenants = load_tenants(cfg.tenants_file)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        backup_before_migration(cfg, migration_head())  # vor Schemaänderungen sichern
        run_migrations(cfg.database_url)
        engine = create_db_engine(cfg.database_url)
        factory = make_session_factory(engine)
        if not cfg.sku_catalog_file.is_file():
            log.warning("SKU-Katalog fehlt: Lizenzen erscheinen mit der Teilenummer")
        # Mitgelieferter Katalog zuerst, die eigene Datei danach: bei gleicher Nummer gewinnt sie.
        sku_entries = load_sku_names(cfg.sku_catalog_file) + load_sku_names(cfg.sku_names_file)
        with factory.begin() as session:
            sync_tenants(session, tenants)
            sync_sku_names(session, sku_entries)
            stale = fail_stale_runs(session)
            ensure_initial_admin(session)
        if stale:
            log.warning("%d unterbrochene Läufe als fehlgeschlagen markiert", stale)
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.settings = cfg
        sso = SsoRuntime(cfg.sso, cfg.secrets_dir, cfg.public_url or "") if cfg.sso else None
        app.state.sso = sso
        scheduler = create_scheduler(factory, cfg) if cfg.scheduler_enabled else None
        app.state.scheduler = scheduler
        if scheduler is not None:
            scheduler.start()
            log.info("Zeitplan gestartet, täglich um %s (%s)", cfg.sync_time, cfg.timezone)
        log.info("Kontenwächter gestartet, %d Mandanten", len(tenants))
        try:
            yield
        finally:
            if sso is not None:
                sso.close()
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            engine.dispose()

    app = FastAPI(
        title="Kontenwächter",
        lifespan=lifespan,
        docs_url=None,  # keine offene API-Doku, bis die Anmeldung steht (Phase 5)
        redoc_url=None,
        openapi_url=None,
    )

    add_auth(app)
    add_deployment_guard(app, cfg.public_url)
    add_security_headers(
        app
    )  # zuletzt hinzugefügt = äußerste Schicht: auch Umleitungen tragen Header
    app.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static"
    )
    web_labels.set_display_timezone(cfg.tzinfo)
    for env in (web_common.templates.env, web_review.templates.env):
        env.globals["tz_name"] = cfg.timezone
    for router in (
        auth_router,
        sso_router,
        pages_router,
        exceptions_router,
        runs_router,
        export_router,
        review_router,
        users_router,
        reports_router,
    ):
        app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health(request: Request, response: Response) -> dict[str, str]:
        """Gesundheitsprüfung: antwortet 200, wenn die Datenbank erreichbar ist, sonst 503.

        Gibt bewusst keine Mandanten- oder Kontodaten preis (Endpunkt ist unauthentifiziert).
        """
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - Health muss jeden Fehler als "ungesund" melden, nie abstürzen
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unhealthy"}
        return {"status": "ok"}

    return app
