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

### Geändert
- Plan: Entscheidungen vom 2. Nov. 2026 übernommen (Zielgruppe nur Softchem-IT, 30 Tage für alles,
  Betrieb auf der Synology, Mail-Bericht in Phase 5, HR-Abgleich zurückgestellt).
- KI-5 geschlossen: CI läuft auf GitHub grün (Tests, `pip-audit`, Container-Smoke-Test).

### Behoben
- Zweiter Start der Anwendung scheiterte mit `table ... already exists`: Die Alembic-Umgebung
  committete `alembic_version` nicht, sodass jeder Start alle Migrationen erneut ausführte.
  Betrifft jeden Neustart mit vorhandener Datenbank (z. B. `docker compose restart`). Die
  Regressionstests prüfen jetzt zweifachen Start und Idempotenz.
