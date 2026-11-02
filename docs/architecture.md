# Architektur (Stand Phase 1)

Gesamtbild und Begründungen: [plan.md](plan.md). Diese Datei beschreibt, was **tatsächlich
implementiert** ist, und wird mit jeder Änderung nachgezogen.

## Laufzeit
Ein Container (`python:3.12-slim`, Nicht-root UID 10001, Dateisystem schreibgeschützt außer `/data`)
startet `uvicorn --factory app.main:create_app`. Beim Start (Lifespan):

1. Logging auf JSON/stdout (`app/logging_config.py`)
2. Mandanten-YAML laden und validieren (`app/config.py`); Fehler brechen den Start ab
3. `alembic upgrade head` (`app/db/session.py`)
4. Mandanten aus der YAML in die Tabelle `tenant` abgleichen (`app/core/tenants.py`):
   anlegen/aktualisieren per Kürzel, fehlende deaktivieren

`GET /health` prüft die Datenbank (`SELECT 1`): 200 `{"status":"ok"}` oder 503. Er gibt keine
Mandantendaten preis. OpenAPI und Doku-Seiten sind abgeschaltet.

## Konfiguration
Umgebungsvariablen `KW_*` (siehe README) und `config/tenants.yaml`. Secrets werden nur über
`read_secret(name, secrets_dir)` aus `/run/secrets/` gelesen; der Name ist auf `[A-Za-z0-9._-]`
beschränkt (kein Pfadausbruch). In der YAML und in der Tabelle steht nur der Secret-*Name*.

## Datenmodell
`app/db/models.py`. Jede Tabelle außer `tenant` und `sku_name` trägt `tenant_id`. Zeitwerte nutzen
`UTCDateTime` (lehnt naive Werte ab, liefert UTC-aware). Enums sind VARCHAR mit CHECK. Constraints
sind benannt (Naming Convention), damit SQLite-Batch-Migrationen sie ansprechen können. Ein Test
(`test_models_and_migrations_are_in_sync`) schlägt fehl, wenn Modell und Migration auseinanderlaufen.
`identity.key` ist über Läufe hinweg stabil (Ausnahmen hängen daran); Rohdaten-Verweise sind
`ON DELETE SET NULL`, damit der 90-Tage-Bereinigungsjob (Phase 4/5) Identitäten nicht beschädigt.

## Migrationen schreiben
```bash
PYTHONPATH=. .venv/bin/python -c "
import tempfile; from alembic import command; from alembic.config import Config
c = Config(); c.set_main_option('script_location','app/db/migrations')
c.set_main_option('sqlalchemy.url', f'sqlite:///{tempfile.mkdtemp()}/x.db')
command.upgrade(c, 'head'); command.revision(c, message='Beschreibung', autogenerate=True)"
```
Danach prüfen und eigene Typen (`UTCDateTime`) durch `sa.DateTime(timezone=True)` ersetzen.

## Noch nicht vorhanden
Connectors (Phase 2/3), Merge/Regeln/Sync/Scheduler/Export/Oberfläche (Phase 4), Anmeldung,
Audit-Log-Nutzung, Backups und Reverse-Proxy (Phase 5).
