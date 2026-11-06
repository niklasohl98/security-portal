# Kontenwächter

Liest täglich alle Benutzer aus **Entra ID** und dem **On-Prem-AD** mehrerer Mandanten (zunächst
CG und CVH), führt sie pro Person zusammen und markiert Konten ohne Anmeldung seit mehr als
30 Tagen. Ergebnis: eine filterbare Liste im Browser, exportierbar als CSV und XLSX.
**Das Tool liest nur.** Es ändert, deaktiviert oder löscht keine Konten.

## Status

Phase 1 (Grundgerüst) und Phase 2 (Entra) sind gemergt und abgenommen. Phase 3 (AD) ist
umgesetzt: Der LDAPS-Connector findet die DCs, prüft sie, liest die Benutzer und das jüngste
`lastLogon` aller DCs und speichert sie; ausgelöst per Befehlszeile (siehe unten), noch ohne
Zeitplan. Der Abnahmelauf gegen die echten Domänen steht noch aus. Die Regeln und die Oberfläche
(Phase 4) sowie Anmeldung und Reverse-Proxy (Phase 5) fehlen noch. **Noch nicht für den
Betrieb mit echten Daten geeignet:** es gibt keine Anmeldung an der Oberfläche. Der Port ist
deshalb nur an `127.0.0.1` gebunden. Details: [ROADMAP.md](ROADMAP.md), [docs/plan.md](docs/plan.md).

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
docker compose exec app python -m app.cli user-add Anna      # weiteren Benutzer anlegen
```

Oberfläche: `http://127.0.0.1:8000/` (nur lokal erreichbar; HTTPS über einen Reverse-Proxy folgt, KI-1).
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

```bash
uv venv --python 3.12 .venv && uv pip sync requirements-dev.txt
```

```bash
.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest --cov
```

Mehr in [CONTRIBUTING.md](CONTRIBUTING.md) und [docs/architecture.md](docs/architecture.md).

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `KW_DATA_DIR` | `/data` | Verzeichnis für SQLite-Datei und Laufdaten |
| `KW_DATABASE_URL` | `sqlite:///<KW_DATA_DIR>/kontenwaechter.db` | SQLAlchemy-URL |
| `KW_TENANTS_FILE` | `/config/tenants.yaml` | Mandanten-YAML (Vorlage: `config/tenants.example.yaml`) |
| `KW_SECRETS_DIR` | `/run/secrets` | Verzeichnis der Docker-Secrets |
| `KW_SKU_NAMES_FILE` | `/config/sku_names.yaml` | Optionale eigene Korrekturen zu den SKU-Namen; hat Vorrang vor dem mitgelieferten Katalog (Vorlage: `config/sku_names.example.yaml`) |
| `KW_LOG_LEVEL` | `INFO` | Log-Stufe |
| `KW_SCHEDULER_ENABLED` | `1` | Täglichen Abruf aller Mandanten im App-Prozess starten (`0` schaltet ihn ab) |
| `KW_SYNC_TIME` | `03:00` | Uhrzeit `HH:MM` des täglichen Abrufs, in `KW_TIMEZONE` |
| `KW_TIMEZONE` | `Europe/Berlin` | IANA-Zeitzone für Zeitplan, Anzeige und Export |
| `KW_SESSION_HOURS` | `8` | Lebensdauer einer Anmeldung (danach neu anmelden) |
| `KW_LOGIN_MAX_ATTEMPTS` | `5` | Fehlversuche bis zur vorübergehenden Sperre eines Kontos |
| `KW_LOGIN_LOCKOUT_MINUTES` | `15` | Dauer dieser Sperre |
| `KW_COOKIE_SECURE` | `auto` | `Secure`-Flag des Sitzungs-Cookies: `auto` (nur bei HTTPS), `1` oder `0` |
| `KW_BACKUP_DIR` | `/backup` | Verzeichnis der Datenbanksicherungen im Container (in `docker-compose.yml` eingehängt) |
| `KW_BACKUP_TIME` | `02:30` | Uhrzeit `HH:MM` der nächtlichen Sicherung, in `KW_TIMEZONE` |
| `KW_BACKUP_KEEP_DAYS` | `14` | So viele Tage bleiben Sicherungen (die neueste immer) |
| `KW_BACKUP_WARN_HOURS` | `36` | Die Übersicht warnt, wenn die letzte gelungene Sicherung älter ist |
| `KW_RETENTION_DAYS` | `90` | So lange bleiben Rohdaten alter Läufe (der jüngste brauchbare Lauf je Quelle bleibt immer) |

## Sicherung und Wiederherstellung

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
