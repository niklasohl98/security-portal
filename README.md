# Kontenwächter

Liest täglich alle Benutzer aus **Entra ID** und dem **On-Prem-AD** mehrerer Mandanten (zunächst
CG und CVH), führt sie pro Person zusammen und markiert Konten ohne Anmeldung seit mehr als
30 Tagen. Ergebnis: eine filterbare Liste im Browser, exportierbar als CSV und XLSX.
**Das Tool liest nur.** Es ändert, deaktiviert oder löscht keine Konten.

## Status

Phasen 1 bis 4 sind umgesetzt und abgenommen: Grundgerüst, Entra, AD, Auswertung und Oberfläche
einschließlich täglichem Zeitplan und Export. In Phase 5 sind lokale Anmeldung, Audit-Log,
Backups, Benutzerverwaltung und Graph-Mailberichte umgesetzt. Niklas hat den einwandfreien
Livebetrieb am 9. November 2026 bestätigt. HTTPS und Entra-SSO sind für die Einrichtung vorbereitet,
SSO bleibt bis zum Deployment deaktiviert. Externe verschlüsselte Backups sind vorbereitet und
bis zur Wahl eines zweiten Ziels ebenfalls deaktiviert. Die Live-Abnahmen bleiben offen;
der Port ist standardmäßig nur an `127.0.0.1` gebunden (KI-1).
Deployment, Zertifikatsinventar, Sicherheitswartung und Abschlussnachweise:
[docs/operations.md](docs/operations.md).
Der [Dokumentationsindex](docs/README.md) führt zielgruppengerecht zu Einrichtung, Betrieb,
Referenzen, Architektur und Entscheidungen.
Das [Nutzerhandbuch](docs/user-guide.md) beschreibt Rollen, Seiten und alle Bedienabläufe.

Strategisch wird das Tool zu einer **IAM- und Lizenzmanagementlösung** ausgebaut: zunächst
verlässliche Bestände und Historie, dann Lizenzkosten/Verträge, Berechtigungsübersicht und
Prüfprozesse. Diese Funktionen sind geplant. Die Entwicklungsreihenfolge, Abhängigkeiten und
Abnahmekriterien stehen in [ROADMAP.md](ROADMAP.md), die Grundentscheidung in
[ADR 0009](docs/adr/0009-iam-und-lizenzmanagement.md). Das ursprüngliche Konzept bleibt unter
[docs/plan.md](docs/plan.md) erhalten. Aktive Änderungen in Entra/AD sind eine gesonderte spätere
Entscheidungsoption; der aktuelle Zugriff bleibt lesend.

## Schnellstart

Voraussetzungen: Docker mit Compose.

```bash
cp config/tenants.example.yaml config/tenants.yaml   # Platzhalterwerte durch eigene ersetzen
```

```bash
docker compose up -d --build
```

```bash
curl http://127.0.0.1:8000/health          # {"status":"ok"}
```

```bash
docker compose exec app python -m app.cli tenants   # angelegte Mandanten
```

Entra-Anbindung (Einrichtung je Mandant: [docs/entra-setup.md](docs/entra-setup.md)):

```bash
docker compose exec app python -m app.cli check-entra cg   # Verbindung testen, ändert nichts
```

```bash
docker compose exec app python -m app.cli sync-entra cg    # Abruf ausführen und speichern
```

AD-Anbindung (Einrichtung je Mandant: [docs/ad-setup.md](docs/ad-setup.md)):

```bash
docker compose exec app python -m app.cli check-ad cg   # DCs finden und prüfen, speichert nichts
```

```bash
docker compose exec app python -m app.cli sync-ad cg    # Abruf ausführen und speichern
```

Auswertung (nach einem Entra- und/oder AD-Abruf; liest nur die Datenbank, kein Zugriff auf die Quellen):

```bash
docker compose exec app python -m app.cli refresh-identities cg   # zusammenführen, Status/Empfehlung berechnen
```

Täglicher Ablauf ohne Zutun: Der App-Prozess ruft um `KW_SYNC_TIME` alle aktiven Mandanten ab (Entra, dann AD),
wertet aus und löscht danach alte Rohdaten. Von Hand geht dasselbe pro Mandant oder nur die Bereinigung:

```bash
docker compose exec app python -m app.cli cycle cg    # Entra + AD abrufen, dann auswerten
docker compose exec app python -m app.cli purge       # Rohdaten älter als KW_RETENTION_DAYS löschen
```

**Erste Anmeldung:** `http://127.0.0.1:8000/` öffnen und mit Benutzername `Admin` und Passwort
`Passwort123` anmelden. Danach verlangt das Tool **sofort ein neues Passwort** (mindestens 12 Zeichen,
nicht der Benutzername, nicht das Startpasswort); vorher ist nichts anderes erreichbar. Das
Startpasswort steht im Quelltext und ist **nicht geheim**: Ändere es gleich beim ersten Start und
mache den Port nicht von außen erreichbar (KI-28). Ist das Passwort vergessen oder das Konto
gesperrt, hilft der Zugang per Befehlszeile (Passwort wird ohne Anzeige abgefragt):

```bash
docker compose exec app python -m app.cli user-list
docker compose exec app python -m app.cli user-reset Admin   # Passwort neu setzen, Sperre aufheben
docker compose exec app python -m app.cli user-add Anna      # weiteren Benutzer anlegen (--admin: mit Verwaltungsrecht)
```

**Benutzerverwaltung:** Admins (Menüpunkt **Benutzer**) legen Benutzer an, deaktivieren sie, geben oder
nehmen die Admin-Rolle und setzen Passwörter zurück. Das Passwort wählt der Admin nie selbst: Das Tool
erzeugt ein **Einmalpasswort** und zeigt es genau einmal an (nicht gespeichert, nicht im Log); der
Benutzer muss es beim ersten Login ändern. Man verwaltet sich nicht selbst, und der letzte aktive Admin
bleibt immer erhalten.

Oberfläche im lokalen Standardbetrieb: `http://127.0.0.1:8000/`. Für den Betrieb über den
vorbereiteten DSM-Reverse-Proxy gilt [docs/https-setup.md](docs/https-setup.md); die Live-Abnahme
steht noch aus (KI-1).
Seiten: **Übersicht** (Kennzahlen, freiwerdende Lizenzen, Warnungen), **Konten** (Filter, Suche),
Kontodetail (Anmeldewerte aus Entra und AD nebeneinander, Ausnahme anlegen, Kontoart setzen),
**Ausnahmen**, **Läufe** (Protokoll und „Jetzt abrufen“) und **Prüfliste** (verschwundene Konten
behalten oder löschen, nur im Tool, nie in Entra oder AD; ADR 0006).
Die Kontenliste exportiert ihre aktuelle Ansicht als **CSV** (Semikolon, UTF-8 mit BOM, öffnet in
Excel direkt) oder **Excel** (Tabelle mit Filtern, Blätter Konten, Lizenzen, Info).

`config/tenants.yaml` steht in `.gitignore`. Dort stehen nur *Namen* von Secrets; die Werte
(Zertifikats-Schlüssel, AD-Passwörter) liegen als Docker-Secrets unter `/run/secrets/`. CA-Dateien
der Domänen liegen unter `config/ca/` (ebenfalls nicht im Repo).

## Entwicklung

HTTPS auf der Synology: [docs/https-setup.md](docs/https-setup.md) beschreibt DNS, DSM-Proxy,
vertrauenswürdige Proxy-Adressen und die Abnahme für `https://accounts.softchem.netz`.
Die Einrichtung im Livebetrieb erfolgt beim Deployment.
SSO über die vorhandene Mail-App: [docs/sso-setup.md](docs/sso-setup.md); alle IDs bleiben
Platzhalter, bis die lokale Deploymentkonfiguration vervollständigt ist.

```bash
uv venv --python 3.12 .venv && uv pip sync requirements-dev.txt
```

```bash
.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest --cov
```

Mehr im [Entwicklungsleitfaden](docs/development.md), in [CONTRIBUTING.md](CONTRIBUTING.md), im
[Dokumentationsstandard](docs/documentation.md) und in [docs/architecture.md](docs/architecture.md).

## Konfiguration

Die vollständige, nach Funktionsbereichen gruppierte Referenz steht in
[docs/configuration.md](docs/configuration.md). Sie enthält alle `KW_*`-Variablen, Defaults,
Grenzwerte, das Schema von `tenants.yaml`, Secret-Regeln und das Zertifikatsinventar. Die
Testsuite gleicht die Referenz automatisch mit Code und `.env.example` ab.

Nicht geheime Vorlage: [`.env.example`](.env.example). Die lokale `.env` wird nicht committed.
Secret-Werte gehören ausschließlich in Docker-Secrets; Konfigurationen speichern nur deren Namen.

## Mail-Bericht

Jeden Montag um 07:30 schickt das Tool je Mandant einen **Wochenbericht** an dessen Empfänger: Zahl der
Konten, markierte Konten, davon lizenziert, freiwerdende Lizenzen je Produkt, offene Prüfungen,
Ausnahmen, Stand der letzten Abrufe und Warnungen. Er enthält **nur Kennzahlen, keine einzelnen
Konten** (ADR 0008). Gesendet wird über **Exchange Online und Microsoft Graph** aus einem eigenen
Softchem-Tenant, mit einer eigenen App-Registrierung (nicht den Zugängen zu den Kunden-Tenants).
Ohne `KW_MAIL_FROM` passiert nichts.

**Einrichten:** Schritt für Schritt in [docs/mail-setup.md](docs/mail-setup.md) (App-Registrierung mit
Zertifikat, Absenderpostfach, Exchange-Berechtigung auf dieses eine Postfach begrenzen, `.env`,
Empfänger je Mandant in `config/tenants.yaml` unter `report.recipients`). Danach prüfen:

```bash
docker compose exec app python -m app.cli mail-check            # Anmeldung der Mail-App (Token)
docker compose exec app python -m app.cli report-preview cg     # Bericht ansehen (sendet nichts)
docker compose exec app python -m app.cli report-send cg        # jetzt senden
```

Admins sehen dasselbe unter **Berichte** (Status, Vorschau, „Jetzt senden“, „Anmeldung prüfen“).
Fehler erscheinen nur als Code (`secret`, `auth`, `forbidden`, `sender`, `rejected`, `throttled`,
`connect`, `send`), nie mit Meldungstext oder Adressen; jeder Versand steht im Audit-Log
(`report.sent`/`report.failed`).

## Sicherung und Wiederherstellung

Externe Sicherung (Restic, HTTPS-REST/S3 oder SFTP), Integritätsprüfung und isolierter Restore:
[docs/external-backup.md](docs/external-backup.md). **Standardmäßig deaktiviert**, bis ein
zweites Ziel bereitsteht. Aktivierung ausschließlich mit `KW_EXTERNAL_BACKUP_ENABLED=1` und
den dort beschriebenen `KW_EXTERNAL_BACKUP_*`-Einstellungen. Ziel-Credentials und das
Verschlüsselungspasswort werden als Docker-Secrets referenziert. Die bestehenden lokalen
Sicherungen laufen unabhängig davon weiter.

Die Datenbank enthält, was sich nicht neu abrufen lässt: Ausnahmen, Audit-Log, Entscheidungen der
Prüfliste und Benutzer. Das Tool sichert sie **jede Nacht um 02:30** (konsistent und geprüft, auch
während der Laufzeit), bewahrt 14 Tage auf und sichert zusätzlich **vor jeder Datenbankmigration**
(`vor-migration-….db`). Die Übersicht warnt, wenn die letzte Sicherung zu alt ist oder fehlschlug.

**Einrichten** (einmalig, im Projektordner): Das Verzeichnis anlegen und dem Container-Benutzer
freigeben. Fehlt das, schlägt die Sicherung mit einer Warnung auf der Übersicht fehl.

```bash
mkdir backup && sudo chown 10001:10001 backup && sudo chmod 700 backup
```

Besser liegt das Verzeichnis **nicht auf demselben Datenträger** wie die Daten: In einer
`docker-compose.override.yml` den Eintrag ersetzen (`!override` braucht Compose ab 2.24) oder, bei
älteren Versionen, `./backup` als Ziel behalten und diesen Ordner mit Hyper Backup oder `rsync` auf
ein anderes Gerät kopieren. Die Dateien enthalten Namen und Passwort-Hashes und sind nicht
verschlüsselt (KI-30): Ordner schützen, am besten auf einem verschlüsselten Freigabeordner.

```bash
docker compose exec app python -m app.cli backup          # sofort sichern (wie nachts)
docker compose exec app python -m app.cli backup-list     # vorhandene Sicherungen
docker compose exec app python -m app.cli backup-verify /backup/kontenwaechter-….db
```

**Wiederherstellen** (die Anwendung muss gestoppt sein; die bisherige Datenbank bleibt als
`….vor-restore-<Zeit>` im Datenordner erhalten):

```bash
docker compose stop app
docker compose run --rm app python -m app.cli restore-backup /backup/kontenwaechter-20261106-023000.db --yes
docker compose up -d
```

Beim Start laufen fehlende Migrationen automatisch (vorher wird wieder gesichert). Prüfe danach
die Übersicht und unter „Läufe“ die Sicherungsliste. Einmal im Quartal lohnt sich eine Probe: eine
Sicherung auf einem Testsystem wiederherstellen.

Die YAML ist die Quelle der Wahrheit für Mandanten und wird bei jedem Start in die Datenbank
übernommen. Mandanten, die dort fehlen, werden deaktiviert, nie gelöscht.
