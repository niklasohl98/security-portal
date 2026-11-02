# Architektur (Stand Phase 2)

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
5. Optionale SKU-Namenstabelle (`sku_names.yaml`) in `sku_name` abgleichen
   (`app/core/sku_names.py`); Einträge nur in der DB bleiben erhalten
6. Unterbrochene Läufe (`running`) als `failed` abschließen (`app/core/sync.py`)

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

## Entra-Abruf (Phase 2)
`app/connectors/graph.py` (nur `GET`, nie Schreibzugriff), `app/core/sync.py` (Lauf).

1. `build_graph_client`: privater Schlüssel aus dem Docker-Secret, Token per `msal`
   (Client-Credentials, Zertifikat mit SHA-1-Fingerabdruck aus der YAML).
2. `fetch_skus` (`/subscribedSkus`), dann `fetch_users` (`/users`, Paging über `@odata.nextLink`).
   Mit `signInActivity` ist `$top` auf 500 begrenzt (Graph-Doku), ohne auf 999.
3. Antwortet Graph auf `signInActivity` mit 403, wird einmal ohne wiederholt: Lauf-Status
   `partial`, Warnung `sign_in_activity_unavailable` (P1/P2 fehlt oder `AuditLog.Read.All`
   nicht erteilt). Ein zweiter 403 ist ein echter Berechtigungsfehler und lässt den Lauf scheitern.
4. Wiederholung bei 429/502/503/504 und Netzwerkfehlern: `Retry-After` (Sekunden, höchstens 300),
   sonst exponentiell (Start 1 s, Obergrenze 60 s), höchstens 5 Wiederholungen.
5. `@odata.nextLink` wird nur befolgt, wenn er auf `https://graph.microsoft.com` (Port 443)
   zeigt; sonst bricht der Abruf ab, damit das Token nie an einen fremden Host geht.
6. Speichern in einer Transaktion: `entra_account` und `tenant_sku` je Lauf, danach Lauf auf
   `success`/`partial` (mit `account_count`, `warnings`). Schlägt der Abruf fehl, steht der Lauf
   auf `failed` mit `error` = Fehlerart (+ Status/Code bei Graph), nie mit Meldungstext.
7. Eine Sperre je (Mandant, Quelle) verhindert Doppelläufe im Prozess (`SyncBusyError`).

`entra_account.licenses` enthält je SKU `sku_id`, `direct`, `group` und `state` (schlechtester
Zustand, sonst `Active`). Kostenlose SKUs werden gespeichert und erst in der Anzeige
ausgeblendet (`sku_name.is_free`, ab Phase 4). Die Konten-Zusammenführung, Status und Flags
kommen in Phase 4; bis dahin liegen nur Rohdaten vor.

## Noch nicht vorhanden
AD-Connector (Phase 3), Merge/Regeln/Scheduler/Export/Oberfläche (Phase 4), Anmeldung,
Audit-Log-Nutzung, Backups und Reverse-Proxy (Phase 5). Der Entra-Abruf läuft bisher nur per
Befehlszeile; Rohdaten-Bereinigung nach 90 Tagen fehlt noch.
