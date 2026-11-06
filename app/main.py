"""FastAPI-Anwendung. Start: ``uvicorn --factory app.main:create_app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import Settings, load_tenants
from app.core.sku_names import load_sku_names, sync_sku_names
from app.core.sync import fail_stale_runs
from app.core.tenants import sync_tenants
from app.db.session import create_db_engine, make_session_factory, run_migrations
from app.logging_config import configure_logging
from app.web.review import router as review_router
from app.web.security import add_security_headers

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
        run_migrations(cfg.database_url)
        engine = create_db_engine(cfg.database_url)
        factory = make_session_factory(engine)
        sku_entries = load_sku_names(cfg.sku_names_file)
        with factory.begin() as session:
            sync_tenants(session, tenants)
            sync_sku_names(session, sku_entries)
            stale = fail_stale_runs(session)
        if stale:
            log.warning("%d unterbrochene Läufe als fehlgeschlagen markiert", stale)
        app.state.engine = engine
        app.state.session_factory = factory
        log.info("Kontenwächter gestartet, %d Mandanten", len(tenants))
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Kontenwächter",
        lifespan=lifespan,
        docs_url=None,  # keine offene API-Doku, bis die Anmeldung steht (Phase 5)
        redoc_url=None,
        openapi_url=None,
    )

    add_security_headers(app)
    app.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static"
    )
    app.include_router(review_router)

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
