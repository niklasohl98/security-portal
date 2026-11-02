# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

## [Unreleased]

### Hinzugefügt
- Phase 1 (Grundgerüst): FastAPI-App mit `/health`, SQLAlchemy-Modelle und Alembic-Migration
  `0001`, Mandantenkonfiguration aus `config/tenants.yaml` (Beispiel für CG und CVH), Laden von
  Docker-Secrets, JSON-Logging, Befehl `python -m app.cli tenants`.
- Dockerfile (Nicht-root, Basis-Image per Digest) und `docker-compose.yml` (schreibgeschütztes
  Dateisystem, Healthcheck, Port nur an `127.0.0.1`).
- CI (Format, Lint, Typen, Tests mit Abdeckung, `pip-audit`, Container-Smoke-Test) und Dependabot.
- Projektdokumentation: Plan, Architektur, ADRs, Roadmap, Backlog, Known Issues, SECURITY.
