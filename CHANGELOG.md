# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionierung nach SemVer.

## [Unreleased]

### Dokumentation
- Projektweite Dokumentationsprüfung: vollständiges Nutzerhandbuch, Datenmodell- und
  Audit-Referenz, Entwicklungsleitfaden und umfassendes Betriebshandbuch ergänzt. Automatische
  Prüfungen gleichen zusätzlich alle Webrouten, SQLAlchemy-Tabellen und statischen Audit-Codes mit
  ihren Referenzen ab; Beitragsregeln und PR-Checkliste verlangen dieselbe Abdeckung.
- Zentraler Dokumentationsindex, vollständige Konfigurations- und CLI-Referenz sowie verbindlicher
  Dokumentationsstandard ergänzt. Architekturstand für HTTPS, Entra-SSO, Zertifikatsinventar und
  externe Sicherung korrigiert und um Systemübersicht/Sicherheitsgrenzen erweitert. Automatische
  Tests verhindern künftig ungültige interne Links sowie undokumentierte `KW_*`-Variablen und
  CLI-Befehle.
- Strategische Entwicklungsroadmap für IAM-Governance und Lizenzmanagement: priorisierte Phasen
  5 bis 11 mit Lieferpaketen, Voraussetzungen, Abnahmekriterien und Erfolgsmessung; Entra-SSO,
  HTTPS und externe Backups integriert. ADR 0009 hält die Produktentscheidung fest; der lesende
  Zugriff auf Kunden-Entra/AD bleibt verbindlich. README, Plan, Architektur, Backlog und
  Entwicklungsanweisungen abgeglichen; Livebetrieb am 9. Nov. 2026 durch Niklas bestätigt.
- `docs/ad-setup.md`: Leserecht des Dienstkontos auf Benutzerobjekte in gehärteten Domänen, mit
  Prüfung je DC (Befund aus dem Abnahmelauf auf der Synology am 5. Nov. 2026).

### Geändert
- Der Kopfbereich zeigt die Sitzung als kompaktes Benutzer-Badge und eine eigenständige
  Abmeldeaktion mit Icon, Hover-/Tastaturfokus und responsivem Layout. Bei Entra-Anmeldung bleibt
  die technische Objekt-ID im Tooltip, statt den sichtbaren Button zu verbreitern.
- Kostenlose SKUs (u. a. `FLOW_FREE`, `POWER_BI_STANDARD`, `TEAMS_FREE`) zählen ohne eigene
  `sku_names.yaml` nicht mehr als Lizenz (vorher galt ohne Datei jede SKU als bezahlt). Gespeicherte
  Empfehlungen ändern sich mit der nächsten Auswertung.
- Der Handelnde im Audit-Log ist jetzt der angemeldete Benutzer (vorher „lokal (ohne Anmeldung)“).
  `POST /runs/<kürzel>/start` verlangt wie alle ändernden Routen das CSRF-Token.
- `/` zeigt die Übersicht (vorher Weiterleitung auf `/review`). Alle Zeiten der Oberfläche erscheinen
  in `KW_TIMEZONE` (Standard Europe/Berlin) statt in UTC.
- `refresh_identities` löscht Identitäten ohne Konto nicht mehr, sondern markiert sie als
  verschwunden (vorher: sofort samt Ausnahmen löschen, KI-20). Die CLI nennt die Zahl der offenen
  Prüfungen.
- DC-Gesundheitscheck erkennt zusätzlich DCs mit leeren Stammdaten normaler Konten
  (`accounts_empty`): Fehlen `userAccountControl`/`whenCreated` bei einem DC für mehr als 1 % der
  Konten, die ein anderer DC liefert, ist er `defective`. Die Referenzobjekte (geschützte Konten)
  erkennen solche Fälle nicht. Nutzt dieselbe `lastLogon`-Suche, keine zusätzliche Abfrage.
  (Im Abnahmelauf am 5. Nov. 2026 löste das keinen DC aus: `LA-CG-GC` liefert dem Dienstkonto alle
  Stammdaten, siehe KI-8.)
- AD-Abruf liest `krbtgt_<Zahl>` (Schlüsselkonten schreibgeschützter DCs) nicht mehr. Das
  Dienstkonto kann sie nicht lesen; sie machten jeden Lauf `partial` (`ad_master_data_incomplete`).
  `krbtgt` selbst bleibt (Referenzobjekt des DC-Checks).

### Behoben
- Entra-SSO mit Zertifikat serialisiert die von MSAL als Bytes erzeugte Client-Assertion vor dem
  Tokenrequest als ASCII-JWT. `httpx` hatte sie zuvor als wörtliches `b'…'` gesendet; Entra lehnte
  den ansonsten erfolgreichen Authorization-Code-Flow mit `AADSTS50027` ab.
- SSO-Fehler nach dem Entra-Callback unterscheiden nun sicher zwischen numerischem AADSTS-Code,
  Signaturschlüsselabruf, Tokenvalidierung, leerem MSAL-Prozesscache und fehlgeschlagener
  Token-Erneuerung. Externe Beschreibungstexte, Tokens und Kontoinhalte bleiben aus dem Log.
- AD-Verbindung: `receive_timeout` war ein Float (60.0), ldap3 packt ihn per `struct.pack('LL', ...)`
  und brach beim Öffnen des Sockets mit `struct.error` ab. Der Wert ist jetzt ein int. Entdeckt im
  Abnahmelauf auf der Synology (KI-14); die Tests hatten immer einen int übergeben, jetzt prüft
  ein Test den Standardwert.

### Hinzugefügt
- Phase 5.3: externe verschlüsselte Sicherung über Restic (HTTPS-REST/S3 oder SFTP), standardmäßig
  deaktiviert. Konsistente DB-/Konfigurationspakete ohne Secrets, eigener Zeitplan, Zielstatus,
  Integritätsprüfung, Restore in ein neues Verzeichnis und ausdrücklich bestätigte Aufbewahrung.
  `external-backup-{init|run|check|restore|prune}`, Einstellungen `KW_EXTERNAL_BACKUP_*`, ADR 0012,
  Anleitung `docs/external-backup.md`. Restic-Sicherheitsbuild aus geprüften Upstream-Quellen
  (ADR 0013); OpenSSH für SFTP im Image. Ein echtes verschlüsseltes Repository wird im
  Integrationstest zurückgeholt. Zweites Ziel und Live-Abnahme stehen noch aus.
- Phase 5.4: optionales Inventar öffentlicher Zertifikate mit Ablaufwarnungen in Übersicht/Läufen
  (`KW_CERTIFICATES_FILE`), JSON-Logging ab dem Serverstart, validierte Proxy-IPs ohne universelle
  Vertrauensliste, CycloneDX-SBOM und Trivy-Gate für behebbare hohe/kritische Image-Befunde.
  Python-Basis aktualisiert, Debian-Sicherheitsupdates und Restic-Sicherheitsbuild mit gesperrten
  Go-Abhängigkeiten. Betriebs-/Recovery-Anleitung `docs/operations.md`, ADR 0013; private Schlüssel
  bleiben außerhalb des Inventars. Zeitlimits beenden auch Restic-SSH-Kindprozesse.
  Wiederherstellungen verwerfen alte Tool-Sitzungen, damit abgemeldete Cookies nicht wieder
  gültig werden; Benutzer und Entscheidungen bleiben erhalten.
- Phase 5.2: optionales Entra-SSO über die vorhandene Mail-App, standardmäßig deaktiviert.
  Code-Flow mit PKCE, Browserbindung, eigener JWT-Signaturprüfung und periodischer Gruppenprüfung;
  Tokens ausschließlich im Prozessspeicher, lokale Admins als Notzugang erhalten. Migration `0009`,
  Einstellungen `KW_SSO_*`, Anleitung `docs/sso-setup.md`, ADR 0011. Reale Werte und Live-Abnahme
  folgen beim Deployment. PyJWT[crypto] ist jetzt direkte Abhängigkeit (Versionen unverändert).
- Phase 5.1: öffentliche HTTPS-Origin `KW_PUBLIC_URL` mit Host-/HTTPS-Prüfung und HSTS,
  explizite Uvicorn-Proxy-Vertrauensliste `KW_TRUSTED_PROXY_IPS`; Healthcheck bleibt lokal.
  DSM-Anleitung für `accounts.softchem.netz` in `docs/https-setup.md`. Live-Einrichtung ausstehend.
- Mail-Bericht (Phase 5, ADR 0008): wöchentlicher Bericht je Mandant über **Exchange Online und
  Microsoft Graph** (`sendMail`) aus einem zusätzlichen Softchem-Tenant, mit eigener App-Registrierung
  und Zertifikat-Login (getrennt von den Zugängen zu den Kunden-Tenants). Die Berechtigung wird in
  Exchange per RBAC for Applications auf ein Postfach begrenzt (`docs/mail-setup.md`). Nur
  Kennzahlen, keine Konten. Empfänger je Mandant in `tenants.yaml` (`report.recipients`, Migration
  `0008`), Zeitplan `KW_REPORT_WEEKDAY`/`KW_REPORT_TIME` (Standard Montag 07:30), Seite **Berichte** für
  Admins (Status, Vorschau, „Jetzt senden“, „Anmeldung prüfen“), Befehle `mail-check`,
  `report-preview`, `report-send`. Neue Einstellungen `KW_MAIL_FROM|TENANT_ID|CLIENT_ID|CERT_SECRET|
  CERT_THUMBPRINT`. Audit-Log: `report.sent`/`report.failed` (nur Zahl, Fehlercode und technischer
  Graph-Code).
- Benutzerverwaltung (Phase 5): Seite `/users` für Admins zum Anlegen, Deaktivieren/Aktivieren,
  Rollenwechsel und Zurücksetzen. Passwörter vergibt der Admin nie selbst: Anlegen und Zurücksetzen
  erzeugen ein **Einmalpasswort** (16 Zeichen), das nur einmal in der Antwort erscheint und beim ersten
  Login geändert werden muss. Neue Rolle `is_admin` (Migration `0007`, bestehende Benutzer werden
  Admin; `Admin` und `user-add --admin` setzen sie). Schutz: keine Änderung am eigenen Konto, der
  letzte aktive Admin bleibt. Audit-Log: `user.activate|deactivate|admin_on|admin_off`.
- Backups (Phase 5): nächtliche, geprüfte Sicherung der SQLite-Datenbank (`app/core/backup.py`, Online-
  Backup-API, Modus 0600, Aufbewahrung 14 Tage, die neueste bleibt) und Sicherung vor jeder
  Datenbankmigration. Übersicht warnt bei fehlender, zu alter oder fehlgeschlagener Sicherung;
  Läufe-Seite listet Sicherungen und hat „Jetzt sichern“. Befehle `backup`, `backup-list`,
  `backup-verify`, `restore-backup --yes`. Neue Einstellungen `KW_BACKUP_DIR`, `KW_BACKUP_TIME`,
  `KW_BACKUP_KEEP_DAYS`, `KW_BACKUP_WARN_HOURS`; `docker-compose.yml` hängt `./backup` ein.
- SKU-Katalog: Lizenzen erscheinen mit lesbaren Namen („Microsoft 365 Business Premium“ statt `SPB`).
  Das Image enthält jetzt `app/data/sku_catalog.yaml` (617 Microsoft-SKUs aus „Product names and service
  plan identifiers for licensing“, 9 als kostenlos gekennzeichnet); `config/sku_names.yaml` hat
  Vorrang. Neuer Befehl `python -m app.cli build-sku-catalog <csv> <ausgabe>` erzeugt den Katalog neu.
  Das Kontodetail zeigt zusätzlich die Teilenummer.
- Anmeldung (Phase 5, ADR 0007): lokale Benutzerdatenbank (`app_user`, `app_session`, Migration
  `0006`), Anmeldeseite `/login`, Abmelden und Passwortwechsel `/password`. Initialkonto `Admin` mit
  Startpasswort `Passwort123` und **erzwungenem Passwortwechsel**. Argon2id-Hash, Sperre nach 5
  Fehlversuchen für 15 Minuten, serverseitige Sitzungen (8 Stunden), CSRF-Token in allen Formularen.
  Alle Seiten außer `/login`, `/health` und `/static` verlangen die Anmeldung. Befehle `user-list`,
  `user-add`, `user-reset`. Neue Einstellungen `KW_SESSION_HOURS`, `KW_LOGIN_MAX_ATTEMPTS`,
  `KW_LOGIN_LOCKOUT_MINUTES`, `KW_COOKIE_SECURE`. Neue Abhängigkeit `argon2-cffi`.
- Phase 4, Etappe 4 (Export): `GET /export?format=csv|xlsx` liefert die aktuelle Filteransicht.
  CSV mit Semikolon, UTF-8 mit BOM und Datum `TT.MM.JJJJ HH:MM`; XLSX mit Blättern Konten (Tabelle
  mit Filtern, farbiger Status), Lizenzen (freiwerdend je SKU) und Info. Schutz gegen
  Formel-Injection, Audit-Log je Export, Links in der Kontenliste. Neue Abhängigkeit `openpyxl`,
  Dev-Abhängigkeit `types-openpyxl`.
- Phase 4, Etappe 3 (Oberfläche): Seiten Übersicht (`/`), Konten (`/accounts`, Filter und Suche),
  Kontodetail (Entra und AD nebeneinander, Lizenzen, Ausnahme anlegen, manuelle Kontoart),
  Ausnahmen (`/exceptions`) und Läufe (`/runs`, „Jetzt abrufen“ im Hintergrund). Datenschicht
  `core/accounts.py`, `core/overview.py`, Service `core/overrides.py`. Zeiten erscheinen in
  `KW_TIMEZONE`. Alle ändernden Routen prüfen Same-Origin und schreiben ins Audit-Log.
- Phase 4, Etappe 2b: Täglicher Abruf im App-Prozess (APScheduler 3.11, `app/core/scheduler.py`,
  `cycle.py`): alle aktiven Mandanten nacheinander (Entra, AD), danach Auswertung und Bereinigung der
  Rohdaten nach `KW_RETENTION_DAYS` (Standard 90; der jüngste brauchbare Lauf je Quelle bleibt). Neue
  Einstellungen `KW_SCHEDULER_ENABLED`, `KW_SYNC_TIME`, `KW_TIMEZONE`, `KW_RETENTION_DAYS`; neue
  Befehle `cycle <kürzel>` und `purge`. Ausnahmen-Service (`app/core/exceptions.py`) mit Grund,
  Ablaufdatum, Audit-Log und sofortiger Neuauswertung; Mandantenwerte `exception_days` (365) und
  `exception_warn_days` (30), Migration `0005`. Neue Abhängigkeiten `apscheduler`, `tzdata`.
- Phase 4, Etappe 2a (Prüfliste, ADR 0006): Verschwundene Identitäten (kein Konto mehr in den
  jüngsten Läufen) werden nicht mehr gelöscht, sondern markiert (`identity.missing_since`,
  Status `missing`; Migration `0004`). Neue Seite `/review` (Jinja2, ohne JavaScript) zum
  Behalten, Löschen (mit Bestätigung) oder Wiederöffnen; jede Entscheidung steht im Audit-Log.
  Neue Abhängigkeit `jinja2`. Sicherheits-Header, Same-Origin-Prüfung für POST.
- Phase 4, Etappe 1 (Logik): `app/core/merge.py` führt Entra- und AD-Konten zusammen
  (`onPremisesImmutableId` = objectGUID, dann sAMAccountName, dann UPN; mehrdeutige Schlüssel
  ordnen nichts zu). `app/core/rules.py` berechnet letzte Anmeldung (Maximum aller Quellen), Status,
  Kontoart und Empfehlung nach `docs/plan.md`. `app/core/identities.py` schreibt das Ergebnis aus
  den jüngsten Läufen in `identity`. Neuer Befehl `python -m app.cli refresh-identities <kürzel>`.
  Status `unknown`, Kontoart `member_onprem` und die Vorgabe `*adm*` für Admin-Konten kommen
  hinzu (siehe `docs/architecture.md`, KI-18 bis KI-20).
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
