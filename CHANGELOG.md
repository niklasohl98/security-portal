# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

## [Unreleased]

### Behoben
- AD-Verbindung: `receive_timeout` war ein Float (60.0), ldap3 packt ihn per `struct.pack('LL', ...)`
  und brach beim Öffnen des Sockets mit `struct.error` ab. Der Wert ist jetzt ein int. Entdeckt im
  Abnahmelauf auf der Synology (KI-14); die Tests hatten immer einen int übergeben, jetzt prüft
  ein Test den Standardwert.

### Hinzugefügt
- Phase 3 (AD): LDAPS-Connector (`app/connectors/ad*.py`, `ldap3`, `dnspython`). DCs per
  DNS-SRV oder feste Liste, Verbindung mit Zertifikatsprüfung (Kette gegen `ad.ca_file`, Hostname,
  Ablauf) und ausdrücklich gesetzter OpenSSL-Sicherheitsstufe, Schreibschutz (ldap3 sperrt nur
  `modify`/`delete`/`modify_dn`, `add` und `extended` sperrt zusätzlich eine eigene Verbindungsklasse).
  Gesundheitscheck je DC über drei Referenzobjekte, Stammdaten vom ersten gesunden DC (leere
  Felder von einem zweiten ergänzt und markiert), `lastLogon` aller erreichbaren DCs (Maximum je
  Konto), Prüfung der Objektanzahl gegen den Median.
- AD-Lauf (`app/core/ad_sync.py`): Speichern in `ad_account`, Lauf-Status `success`/`partial`/`failed`
  mit `dcs_checked`, `dcs_defective` und Warnungen. Befehle `python -m app.cli check-ad <kürzel>`
  (Trockenlauf) und `sync-ad <kürzel>`.
- Mandantenfelder `ad.ca_file` und `ad.accept_weak_dc_keys_until` (befristete, ausdrückliche
  Ausnahme für DC-Zertifikate mit RSA unter 2048 Bit, ADR 0005); DC-Namen werden validiert.
  Migration `0003`.
- Einrichtungsanleitung `docs/ad-setup.md`, ADR 0005.
- Phase 2 (Entra): Graph-Connector (`app/connectors/graph.py`) mit Zertifikats-Login (msal),
  Paging, Wiederholung bei 429/502/503/504 mit `Retry-After`/Backoff und Schutz vor fremden
  `@odata.nextLink`-Hosts. Liest Benutzer (inkl. `signInActivity` und Lizenzen), `subscribedSkus`
  und Organisation. Ohne P1 läuft der Abruf ohne Anmeldedaten weiter (Status `partial`).
- Lauf-Ablauf (`app/core/sync.py`): Lauf je Mandant und Quelle mit Sperre gegen Doppelläufe,
  Speichern in `entra_account` und neuer Tabelle `tenant_sku`, Lauf-Warnungen (`sync_run.warnings`),
  Abschluss unterbrochener Läufe beim Start. Migration `0002`.
- Befehle `python -m app.cli check-entra <kürzel>` und `sync-entra <kürzel>`.
- SKU-Namenstabelle aus `config/sku_names.yaml` (Vorlage `config/sku_names.example.yaml`).
- Einrichtungsanleitung `docs/entra-setup.md`.
- Phase 1 (Grundgerüst): FastAPI-App mit `/health`, SQLAlchemy-Modelle und Alembic-Migration
  `0001`, Mandantenkonfiguration aus `config/tenants.yaml` (Beispiel für CG und CVH), Laden von
  Docker-Secrets, JSON-Logging, Befehl `python -m app.cli tenants`.
- Dockerfile (Nicht-root, Basis-Image per Digest) und `docker-compose.yml` (schreibgeschütztes
  Dateisystem, Healthcheck, Port nur an `127.0.0.1`).
- CI (Format, Lint, Typen, Tests mit Abdeckung, `pip-audit`, Container-Smoke-Test) und Dependabot.
- Projektdokumentation: Plan, Architektur, ADRs, Roadmap, Backlog, Known Issues, SECURITY.

### Geändert
- `docs/entra-setup.md`: Zertifikat auch unter Windows/PowerShell erzeugen, Secret auf der Synology
  ablegen (Besitzer `10001`, Modus `0400`), Hinweis zum Start der App vor den CLI-Befehlen und
  neue Fehlerbilder. Phase 2 nach der Abnahme auf der Synology als abgeschlossen vermerkt.
- Arbeitsweise: Von Claude geöffnete PRs werden bei grüner CI automatisch gemergt (Merge-Commit);
  `CLAUDE.md` und `CONTRIBUTING.md` angepasst.
- Plan: Entscheidungen vom 2. Nov. 2026 übernommen (Zielgruppe nur Softchem-IT, 30 Tage für alles,
  Betrieb auf der Synology, Mail-Bericht in Phase 5, HR-Abgleich zurückgestellt).
- KI-5 geschlossen: CI läuft auf GitHub grün (Tests, `pip-audit`, Container-Smoke-Test).

### Behoben
- Zweiter Start der Anwendung scheiterte mit `table ... already exists`: Die Alembic-Umgebung
  committete `alembic_version` nicht, sodass jeder Start alle Migrationen erneut ausführte.
  Betrifft jeden Neustart mit vorhandener Datenbank (z. B. `docker compose restart`). Die
  Regressionstests prüfen jetzt zweifachen Start und Idempotenz.
