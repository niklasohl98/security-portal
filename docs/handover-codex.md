# Übergabe an Codex: Kontenwächter

Stand: 9. Nov. 2026. Basis: `main` = `7105db0` (Merge von PR 22 am 6. Nov. 2026).
Diese Datei ist eine Momentaufnahme. Bei Widerspruch gelten Code und `git log`.

**Kennzeichnung:** *[geprüft]* = in dieser Sitzung am Code, am Git-Stand oder durch einen Lauf
nachgesehen. *[Doku]* = steht in den Projektdateien, wurde nicht gegen das laufende System geprüft.
*[Annahme]* = ungeprüft, vor dem Verlassen darauf erst prüfen.

Zugangsdaten stehen nicht in dieser Datei und gehören nie ins Repo. Wo ein Geheimnis vorkommt, steht
nur sein Name oder der Ort, an dem es liegt.

---

## 1. Zweck und harte Grenzen

Lokales Sicherheitstool (Docker, Betrieb auf einer Synology). Es liest Benutzer aus **Entra ID**
(Microsoft Graph) und aus dem **On-Prem-AD** (LDAPS) mehrerer Mandanten (bisher CG und CVH), führt sie
pro Person zusammen und markiert Konten ohne Anmeldung seit mehr als 30 Tagen. Nutzer sind nur die
Mitarbeitenden der Softchem-IT. *[Doku: `CLAUDE.md`, `README.md`]*

Regeln, die jede Änderung einhalten muss (Quelle `CLAUDE.md`):

- **Nur lesend.** Nie Konten in Entra oder AD ändern, deaktivieren oder löschen. "Löschen" in der
  Prüfliste betrifft nur Zeilen der Tool-Datenbank.
- Jede Tabelle außer `tenant`, `sku_name`, `app_user`, `app_session` hat `tenant_id`; jede Abfrage
  filtert danach.
- Geheimnisse nur als Docker-Secrets unter `/run/secrets/<name>`; Datenbank und YAML kennen nur den Namen.
- Letzte Anmeldung = Maximum aus Entra `lastSuccessfulSignIn`, Entra `lastNonInteractiveSignIn`,
  AD `lastLogon` (alle DCs), AD `lastLogonTimestamp`. `lastSignInDateTime` wird nur angezeigt.
- LDAPS immer mit Zertifikatsprüfung, nie `verify=False`, nie LDAP ohne TLS.
- Zeiten immer timezone-aware (UTC). Fristen aus der Mandantenkonfiguration.
- Logs: feste Texte mit Platzhaltern, keine Namen, UPNs, Adressen, Tokens, Eingabewerte.
- Keine echten Mandanten- oder Personendaten in Fixtures, Tests, Commits.
- Keine Versionen, Hashes oder API-Felder erfinden.
- Verhaltensänderung und Refactoring nie im selben PR. Doku gehört in denselben PR wie die Änderung.

## 2. Git-Stand *[geprüft]*

| Punkt | Befund |
|---|---|
| Aktueller Branch | `claude/sweet-meitner-p6hu0q` |
| HEAD | `7105db0`, identisch mit `origin/main`, `origin/claude/sweet-meitner-p6hu0q` und `origin/claude/vigilant-archimedes-7e77x6` |
| Arbeitsverzeichnis | sauber, keine Stashes (vor Anlage dieser Datei) |
| Nicht committiert | nur diese Datei `docs/handover-codex.md` (und das lokale, ignorierte `.venv/`) |
| Historie | 71 Commits, Merge-Commits je PR (Projektregel: Merge-Commit, kein Squash) |
| Offene PRs | 23 bis 27, alle Dependabot (siehe 7) |
| Alte Claude-Branches | `claude/determined-hopper-gsuygp`, `claude/magical-feynman-mg1at3`, `claude/magical-gates-o63xp9`, `claude/vigilant-archimedes-7e77x6`: vollständig in `main` enthalten, können gelöscht werden *[Annahme: Löschen nicht abgestimmt]* |
| Remote | `origin` = `github.com/niklasohl98/security-portal` |

Die Zuordnung "Branch gehört zu Phase X" ist aus den Merge-Commits abgeleitet, nicht aus Branchnamen.

## 3. Qualitätsstand *[geprüft in dieser Sitzung, Python 3.12, frisches `.venv`]*

| Prüfung | Ergebnis |
|---|---|
| `ruff format --check .` | 138 Dateien formatiert |
| `ruff check .` | keine Befunde |
| `mypy` (strict) | keine Fehler in 107 Quelldateien |
| `pytest --cov` | **684 bestanden**, 3 Warnungen, Abdeckung **98,67 %** (Untergrenze 95 %, nie senken), Laufzeit etwa 107 s |

Die 3 Warnungen sind `StarletteDeprecationWarning` für `httpx` im `TestClient` (KI-3), `pyasn1`-
Deprecations aus `ldap3` (KI-15) und eine `ResourceWarning` aus dem TLS-Test `test_unknown_ca_is_rejected`.

**Nicht geprüft:** Docker-Build und Container-Smoke-Test (in der Sandbox scheitert der Build am
Proxy-CA, `CONTRIBUTING.md`); laufender Betrieb auf der Synology; Live-Verbindungen zu Entra, AD und
Exchange (die Sandbox hat keine Zugänge, und es gehört sich nicht, welche zu beschaffen).

## 4. Architektur

Ein Container (`python:3.12-slim`, Basis per Digest gepinnt, Nicht-root UID 10001, Dateisystem
schreibgeschützt außer `/data` und `/tmp`, `cap_drop: ALL`). Ein Prozess: FastAPI/Uvicorn mit
APScheduler (3.x, `BackgroundScheduler`) im selben Prozess. SQLite (WAL) in `/data`, Sicherungen
nach `/backup`, Konfiguration schreibgeschützt aus `/config`. *[geprüft: `Dockerfile`, `docker-compose.yml`]*

```
Entra (Graph, Zertifikat) ─► connectors/graph.py ─► core/sync.py ──┐
AD (LDAPS, ldap3) ─────────► connectors/ad*.py  ─► core/ad_sync.py ─┤ entra_account / ad_account (Rohdaten je Lauf)
                                                                     ▼
                              core/merge.py + rules.py + identities.py ─► identity (Status, Flag, Empfehlung)
                                                                     ▼
              web/* (Jinja2, kein JS) · export/* (CSV, XLSX) · core/report.py ─► mail/transport.py (Graph sendMail)
```

Schichten *[geprüft: Dateibaum]*:

| Pfad | Inhalt |
|---|---|
| `app/main.py` | `create_app()`: Lifespan, Router, Middleware |
| `app/config.py` | `Settings` (`KW_*`), Mandanten-YAML mit pydantic-Validierung, `read_secret` |
| `app/cli.py`, `app/cli_mail.py` | Befehlszeile `python -m app.cli …` |
| `app/db/` | `models.py`, `session.py` (Engine, Migrationen), `migrations/versions/0001…0008` |
| `app/connectors/` | `graph.py` (nur GET), `ad_client.py` (LDAPS, doppelt schreibgeschützt), `ad_parse.py`, `ad.py` (DC-Erkennung, Gesundheitscheck), `check.py` |
| `app/core/` | `sync.py`, `ad_sync.py`, `merge.py`, `rules.py`, `identities.py`, `accounts.py`, `overview.py`, `overrides.py`, `exceptions.py`, `review.py`, `cycle.py`, `scheduler.py`, `retention.py`, `backup.py`, `report.py`, `sku_catalog.py`, `sku_names.py`, `tenants.py` |
| `app/auth/` | lokale Benutzer, Argon2id, Sitzungen, Middleware, Benutzerverwaltung |
| `app/web/` | Routen je Seite, `common.py`, `filters.py`, `labels.py`, `security.py`, `templates/`, `static/` |
| `app/export/` | `columns.py`, `csv.py`, `xlsx.py` |
| `app/mail/` | `settings.py`, `transport.py` |
| `app/data/sku_catalog.yaml` | mitgelieferter SKU-Katalog (617 SKUs) |
| `tests/` | 40 Testdateien plus Hilfsmodule (`*_fakes.py`, `conftest.py`), gemockte Graph-/LDAP-Antworten (respx, ldap3-Mock, lokaler TLS-Server) |

Wichtige Abläufe (Details: `docs/architecture.md`):

1. **Start (Lifespan in `app/main.py`):** Logging → Mandanten-YAML laden (Fehler brechen den Start ab) →
   Sicherung vor Migration, falls Schema veraltet → `alembic upgrade head` → Mandanten und SKU-Namen
   abgleichen → unterbrochene Läufe als `failed` markieren → Initialkonto anlegen, wenn die Benutzer-
   tabelle leer ist → Scheduler starten.
2. **Tageszyklus (`KW_SYNC_TIME`, Standard 03:00 Europe/Berlin):** je aktiver Mandant Entra, dann AD,
   dann `refresh_identities`; danach Bereinigung der Rohdaten (Standard 90 Tage). Ein Fehler stoppt
   weder die andere Quelle noch andere Mandanten.
3. **Weitere Zeitplan-Jobs:** Sicherung (`KW_BACKUP_TIME`, Standard 02:30) und Wochenbericht
   (`KW_REPORT_WEEKDAY`/`KW_REPORT_TIME`, nur mit gesetztem `KW_MAIL_FROM`). *[geprüft: drei `add_job`-Aufrufe
   in `app/core/scheduler.py`; Standardwerte aus `docs/architecture.md`, `docs/adr/0008`]*
4. **Zuordnung Entra ↔ AD:** `onPremisesImmutableId` = `objectGUID`, dann `sAMAccountName`, dann UPN;
   bei mehrdeutigem Schlüssel wird nichts zugeordnet.
5. **Status in fester Reihenfolge:** `exception`, `new`, `disabled`, `unknown`, `never`, `inactive_long`,
   `inactive`, `active`. `unknown` ist eine Ergänzung gegenüber dem Plan (KI-18).
6. **AD-Gesundheitscheck:** vor jedem Lauf; DC mit leerer `userAccountControl`/`whenCreated` gilt als
   defekt und liefert keine Stammdaten, `lastLogon` wird trotzdem von allen erreichbaren DCs gelesen.

## 5. Einstiegspunkte

| Zweck | Datei und Symbol |
|---|---|
| Webanwendung | `app/main.py` → `create_app()`, Start `uvicorn --factory app.main:create_app` (`Dockerfile`) |
| Befehlszeile | `app/cli.py` → `main()` (Unterbefehle siehe 8), Mail-Befehle in `app/cli_mail.py` |
| Gesundheitsprüfung | `GET /health` (prüft die DB, keine Mandantendaten) |
| Zeitplan | `app/core/scheduler.py` → `create_scheduler`, `run_nightly` in `core/cycle.py` |
| Entra-Abruf | `app/core/sync.py`, `app/connectors/graph.py` |
| AD-Abruf | `app/core/ad_sync.py`, `app/connectors/ad.py`, `ad_client.py` |
| Auswertung | `app/core/identities.py` → `refresh_identities`; Regeln in `rules.py`, Zuordnung in `merge.py` |
| Anmeldung | `app/auth/middleware.py` → `add_auth`; `app/auth/service.py` |
| Sicherheits-Header, CSRF | `app/web/security.py`, `app/web/common.py` |
| Konfigurationsmodell | `app/config.py`, Vorlagen `config/tenants.example.yaml`, `.env.example` |
| Datenmodell | `app/db/models.py`; Test `test_models_and_migrations_are_in_sync` |

Seiten: `/` Übersicht, `/accounts`, `/accounts/<kürzel>/<id>`, `/exceptions`, `/runs`, `/review`,
`/export`, `/users` (Admin), `/reports` (Admin), `/login`, `/password`. Ohne gültige Sitzung sind nur
`/login`, `/health` und `/static/*` erreichbar. *[Doku, Routen-Dateien im Baum vorhanden]*

## 6. Entwicklungsstand

Quelle `ROADMAP.md`, abgeglichen mit dem Git-Verlauf:

| Phase | Stand |
|---|---|
| 1 Grundgerüst | fertig, abgenommen |
| 2 Entra | fertig; am 3. Nov. 2026 auf der Synology gegen CG und CVH abgenommen *[Doku]* |
| 3 AD | fertig; Abnahmelauf am 5. Nov., Abnahme am 6. Nov. 2026 *[Doku]* |
| 4 Logik und Oberfläche | fertig, am 6. Nov. 2026 abgenommen (PR 13 bis 19) *[Doku]* |
| 5 Härtung und Ausbau | **begonnen**: fertig sind lokale Anmeldung (ADR 0007), Audit-Log, nächtliche Sicherung mit Wiederherstellung, Benutzerverwaltung mit Admin-Rolle und Einmalpasswörtern, Mail-Bericht über Exchange Online (ADR 0008). Offen: Entra-OIDC, Reverse-Proxy mit HTTPS |

Zuletzt gemergt (PR 20 bis 22, alle am 6. Nov. 2026): Backups, Benutzerverwaltung, Mail-Bericht.
Danach kam bis zum 9. Nov. nichts außer den Dependabot-PRs. *[geprüft: `git log`, PR-Liste]*

Nächste strategische Ausrichtung laut `ROADMAP.md`: "Richtung Lizenzmanagement". Das ist dort nur als
Satz vermerkt, es gibt weder Plan noch Code dazu. *[geprüft: Suche in Roadmap/Plan]*

## 7. Offene Änderungen

**Im Arbeitsverzeichnis:** keine außer dieser Datei.

**Offene PRs auf GitHub** *[geprüft: Liste; CI nur für PR 26 abgerufen]*:

| PR | Änderung | Stand |
|---|---|---|
| 23 | dnspython 2.9.1 → 2.9.2 | CI nicht abgefragt |
| 24 | ruff 0.17.3 → 0.17.4 | CI nicht abgefragt |
| 25 | pydantic 2.14.2 → 2.14.3 | CI nicht abgefragt |
| 26 | sqlalchemy 2.1.6 → 2.1.8 | CI grün (alle drei Jobs) |
| 27 | pip 26.2.2 → 26.3 (dev) | CI nicht abgefragt |

Die Auto-Merge-Freigabe in `CLAUDE.md` gilt für **eigene** PRs des Assistenten bei grüner CI. Für
Dependabot-PRs ist sie *[Annahme]* nicht erteilt. Vor dem Mergen Niklas fragen. Jeder dieser PRs ändert
die Lockfiles mit Hashes; nach dem Merge einzeln nicht neu erzeugen, sondern pro PR prüfen.

## 8. Start, Test und Deployment

### Lokal entwickeln *[geprüft, funktioniert in der Sandbox]*

```bash
uv venv --python 3.12 .venv && uv pip sync requirements-dev.txt
```

Vor jedem Push alle vier Befehle (entsprechen der CI):

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest --cov
```

(Die CI verwendet `ruff format --check .`; lokal formatiert der Befehl oben.)

Lockfiles neu erzeugen, nur nach Änderung in `pyproject.toml`:

```bash
uv pip compile pyproject.toml --generate-hashes --python-version 3.12 -o requirements.txt
```

```bash
uv pip compile pyproject.toml --group dev --generate-hashes --python-version 3.12 -o requirements-dev.txt
```

### Migration nach Modelländerung

Kein `alembic.ini`. Rezept in `docs/architecture.md` ("Migrationen schreiben"). Danach `UTCDateTime`
in der erzeugten Datei durch `sa.DateTime(timezone=True)` ersetzen, sonst schlägt
`test_models_and_migrations_are_in_sync` fehl. Der nächste freie Index ist `0009`.

### Start als Container *[Doku; hier nicht geprüft]*

```bash
cp config/tenants.example.yaml config/tenants.yaml
```

```bash
docker compose up -d --build
```

Prüfen mit `curl http://127.0.0.1:8000/health`. Der Port ist nur an `127.0.0.1` gebunden. Mandanten-
Zugangsdaten kommen als Docker-Secrets über eine lokale, ignorierte `docker-compose.override.yml`
(Anleitung `docs/entra-setup.md`, `docs/ad-setup.md`, `docs/mail-setup.md`). Die Sicherungsablage
`./backup` vorher anlegen und `10001:10001` zuweisen.

CLI im Container: `docker compose exec app python -m app.cli <befehl>`. Befehle *[geprüft:
`app/cli.py`]*: `tenants`, `check-entra|sync-entra <kürzel>`, `check-ad|sync-ad <kürzel>`,
`refresh-identities <kürzel>`, `cycle <kürzel>`, `purge`, `user-list|user-add|user-reset`,
`backup`, `backup-list`, `backup-verify <datei>`, `restore-backup <datei> --yes`,
`mail-check`, `report-preview|report-send <kürzel>`, `build-sku-catalog`.

### CI (`.github/workflows/ci.yml`) *[geprüft]*

Drei Jobs: `test` (Format, Lint, mypy, pytest mit Abdeckung), `audit` (`pip-audit` gegen
`requirements.txt`), `container` (Image bauen, Smoke-Test mit Beispielkonfiguration, Nicht-root,
Neustart ohne erneute Migration, Entra-Abruf ohne Secret scheitert sauber, Dateisystem schreibgeschützt).
Actions sind per Commit-Hash gepinnt.

### Deployment

Betrieb als Compose-Projekt auf der Synology (ADR 0004). Es gibt **keinen automatisierten Deploy**:
Update = Repo holen, `docker compose up -d --build`. Beim Start sichert das Tool die Datenbank vor einer
Migration. *[Doku]* Offen und nicht dokumentiert: DSM-Version, CPU-Architektur, Container-Manager-
Unterstützung für `secrets`/`read_only`/`cap_drop` (`docs/plan.md`, offene Punkte) *[Annahme: nicht geklärt]*.

## 9. Getroffene Entscheidungen und Begründung

| Entscheidung | Begründung | Quelle |
|---|---|---|
| Ein Container, SQLite, APScheduler im Prozess, Jinja2 ohne JS-Build | wenig bewegliche Teile, kleines Image, Dauerdienst für wenige Nutzer | ADR 0001 |
| AD direkt per LDAPS statt Windows-Agent | weniger Komponenten; Rückfall nur, falls Port 636 nicht erreichbar wäre (Netz ist erreichbar) | ADR 0002 |
| Graph-Zugang per Zertifikat statt Client-Secret, Geheimnisse als Docker-Secrets | Secrets laufen ab und liegen leichter im Klartext herum; DB/YAML kennen nur den Namen | ADR 0003 |
| Betrieb auf der Synology, nur Softchem-IT als Nutzer, kein Kunden-Rollenmodell | Zielgruppe klar; `tenant_id`-Trennung bleibt trotzdem Pflicht | ADR 0004 |
| Befristete Ausnahme für RSA-1024-Schlüssel der CG-Domänencontroller (`ad.accept_weak_dc_keys_until`) | Neuausstellung entfällt, Umgebung wird abgelöst; Kette, Hostname und Ablauf bleiben geprüft, es gibt keinen Schalter ohne Prüfung | ADR 0005, KI-13 |
| Verschwundene Identitäten markieren und von Menschen prüfen lassen, nie automatisch löschen | ein unvollständiger Abruf würde sonst Ausnahmen und Entscheidungen vernichten | ADR 0006 (6. Nov. 2026) |
| Lokale Anmeldung (Argon2id, serverseitige Sitzungen, CSRF-Token, Sperre nach 5 Fehlversuchen) vorab, Entra-OIDC später | OIDC braucht die App-Registrierung und Sicherheitsgruppe im Softchem-Tenant | ADR 0007 |
| Startpasswort des Kontos `Admin` im Quelltext mit erzwungenem Wechsel | ausdrücklicher Wunsch von Niklas; Risiko bekannt und dokumentiert | ADR 0007, KI-28 |
| Mail-Bericht über Graph `sendMail` mit eigener App im Softchem-Tenant, Berechtigung per Exchange RBAC for Applications auf ein Postfach, nur Kennzahlen, keine automatische Wiederholung | `Mail.Send` in Entra gilt tenantweit; Mails verlassen das Tool, daher keine Namen; `sendMail` ist nicht idempotent | ADR 0008 |
| Schwellwert 30 Tage für alle Kontoarten | Entscheidung vom 2. Nov. 2026, je Mandant überschreibbar, nicht je Kontoart | `docs/plan.md` |
| Status `unknown` ohne `signInActivity` | sonst stünde nach einem Lauf ohne P1-Lizenz jedes Konto auf "nie angemeldet" | KI-18 |
| Merge-Commits statt Squash | getrennte Commits (Fix vor Feature) bleiben erhalten | `CONTRIBUTING.md` |

## 10. Verworfene Ansätze *[Doku: `BACKLOG.md`, ADRs]*

- Konten automatisch deaktivieren oder löschen: das Tool ist ein Wächter, kein Akteur.
- Client-Secret statt Zertifikat für Graph.
- Windows-Agent mit PowerShell statt LDAPS (nur Rückfall).
- LDAP ohne TLS (Port 389).
- Graph-SDK (msal + httpx genügen).
- JavaScript-Build / SPA.
- Rollenmodell mit Mandantensicht für Kunden; Schwellwert je Kontoart.
- Sitzung als signiertes Cookie ohne Datenbank (kein serverseitiges Beenden möglich); scrypt statt Argon2id.
- SMTP-Relay für den Mail-Bericht (der Versand wurde in `439bf17` auf Graph umgestellt).
- HR-Abgleich per Excel-Upload: zurückgestellt, bei Bedarf neu bewerten.

## 11. Bekannte Fehler und Einschränkungen

Vollständig in `KNOWN_ISSUES.md` (KI-1 bis KI-31, 31 Zeilen; KI-31 steht in der Datei vor KI-30).
Die offenen, relevanten:

| ID | Schwere | Kurz |
|---|---|---|
| KI-28 | hoch | Startpasswort von `Admin` ist öffentlich bekannt; bis zur ersten Anmeldung kann jeder, der die Oberfläche erreicht, das Konto übernehmen. Port nicht freigeben |
| KI-1 | mittel | Kein HTTPS: Passwort und Cookie gehen im Klartext, sobald der Port nicht nur lokal erreichbar ist. Reverse-Proxy fehlt |
| KI-13 | mittel | CG-DCs mit RSA-1024; Ausnahme befristet, **vor 01.01.2027 (erster DC-Zertifikatsablauf) neu entscheiden** |
| KI-6, KI-30 | mittel | Sicherungen liegen auf demselben Gerät und sind unverschlüsselt (enthalten Namen und Passwort-Hashes) |
| KI-18 | mittel | Ohne P1/`signInActivity` werden reine Cloud-Konten nicht geflaggt |
| KI-21 | mittel | Prüfliste ohne Sammelaktion |
| KI-29 | mittel | Kein zweiter Faktor; Admin-Konto durch Fehlversuche für 15 Minuten sperrbar |
| KI-19 | niedrig | Namensmuster `*adm*` trifft auch "Adam"; je Mandant `account_patterns.admin` schärfen |
| KI-11, KI-15, KI-16, KI-17, KI-23, KI-24 bis KI-27, KI-31 | niedrig | siehe Datei (u. a. SHA-1-Fingerabdruck, `ldap3` unwartbar alt, kein Zertifikatsablauf-Hinweis, keine Mandantenseite, keine API) |

**Abweichungen zwischen Doku und Code**, die beim Lesen *[geprüft]* aufgefallen sind (keine davon
verändert, da dies kein Auftrag war):

1. `CLAUDE.md` nennt `auth/` als "später"; es existiert und ist umgesetzt.
2. `README.md` Abschnitt "Status" sagt, der Abnahmelauf von Phase 3 stehe noch aus und der Abruf laufe
   "noch ohne Zeitplan". Beides ist überholt (`ROADMAP.md`, `architecture.md`).
3. ADR 0007 sagt "keine Benutzerverwaltung in der Oberfläche"; sie gibt es seit PR 21.
4. ADR 0001 nennt HTMX und Caddy. HTMX wird nicht verwendet (`architecture.md`), Caddy ist noch nicht da.
5. ADR 0003 verspricht eine tägliche Prüfung des Zertifikatsablaufs mit Warnung 30 Tage vorher. Das ist
   **nicht umgesetzt** (KI-24).
6. `app/cli.py` hat 438 Zeilen, `app/connectors/ad.py` 402; die Richtgröße in `CLAUDE.md` ist etwa 400.
7. Sicherungsdateien erhalten laut Doku Modus 0600 und der Ordner soll `chmod 700` sein. Ob der
   eingehängte Ordner auf der Synology so eingestellt ist, ist *[Annahme]*.

## 12. Nächste konkrete Schritte

Reihenfolge ist eine Empfehlung; Priorität setzt Niklas.

1. **Dependabot-PRs 23 bis 27 sichten:** CI jeweils prüfen, mit Niklas klären, ob gemergt werden darf.
   Danach prüfen, dass `pip-audit` weiter grün ist.
2. **Doku-Abweichungen aus Abschnitt 11 bereinigen** (nur Text, eigener PR, `docs(...)`-Commit).
3. **Entra-OIDC-Anmeldung (Phase 5):** setzt die App-Registrierung (delegiert) und eine
   Sicherheitsgruppe im Softchem-Tenant voraus, beides offen (`docs/plan.md`). Ohne diese Vorarbeit
   nicht beginnen. Wahrscheinlich neues ADR 0009; die lokale Anmeldung als Notzugang erhalten
   (ADR 0007, Alternativen). Neue Tabellen oder Felder brauchen Migration `0009`.
4. **Reverse-Proxy mit HTTPS** (Caddy im Compose oder DSM-Proxy, ADR 0004) und danach
   `KW_COOKIE_SECURE=1`; erst dann den Port freigeben (KI-1, KI-28).
5. **Livetest des Mail-Berichts:** App-Registrierung, Absenderpostfach und echte Empfänger fehlen
   (`docs/mail-setup.md`). Bis dahin ist der Versand nur gegen Mocks getestet *[geprüft: Tests mocken
   Graph]*.
6. **Backup-Ziel** außerhalb der Synology festlegen, ggf. Verschlüsselung (KI-6, KI-30).
7. **CG-Schlüsselausnahme** vor dem 01.01.2027 neu bewerten (KI-13, ADR 0005); Enddatum in der echten
   `tenants.yaml` prüfen (die Datei liegt nicht im Repo).
8. **Kleinere Backlog-Punkte** (`BACKLOG.md`): Sammelaktion "alle behalten" (KI-21), Mandantenseite
   (KI-25), Verlauf je Konto (KI-26), Zertifikatsablauf-Warnung (KI-24), `web/review.py` auf
   `web/common.py` umstellen (reines Refactoring, eigener PR), Trivy in der CI.
9. **Strategische Richtung Lizenzmanagement:** erst klären, was gemeint ist, dann Plan in
   `docs/plan.md`. Keine Umsetzung ohne Vorgabe.

## 13. Relevante Dateien in der Reihenfolge des Lesens

1. `CLAUDE.md`, `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md` (Regeln, Definition of Done)
2. `docs/architecture.md` (was tatsächlich implementiert ist), `docs/plan.md` (Zielbild)
3. `ROADMAP.md`, `BACKLOG.md`, `KNOWN_ISSUES.md`, `CHANGELOG.md`
4. `docs/adr/0001` bis `0008`
5. `app/main.py`, `app/config.py`, `app/db/models.py`
6. `app/core/rules.py`, `merge.py`, `identities.py`
7. `docs/entra-setup.md`, `docs/ad-setup.md`, `docs/mail-setup.md` (Betrieb und Einrichtung)
8. `tests/*_fakes.py`, `tests/conftest.py` (Testhilfen)

## 14. Ungeprüfte Annahmen (Sammelliste)

- Der Betrieb auf der Synology läuft auf dem Stand von `main` (zuletzt dokumentiert: Abnahme 6. Nov. 2026).
- Die Aussagen aus den Abnahmeläufen (Kontenzahlen, DC-Zahlen) stammen aus `ROADMAP.md`/`KNOWN_ISSUES.md`
  und wurden nicht erneut gemessen.
- Docker-Build und Smoke-Test sind lokal nicht gelaufen; die grüne CI wurde nur für PR 26 gesehen.
- Mail-Versand, Entra- und AD-Zugang wurden gegen keinen echten Dienst getestet.
- Standardwerte der `KW_*`-Variablen wurden aus Doku, `docker-compose.yml` und `.env.example` übernommen,
  nicht jeder einzelne im Code nachgelesen.
- Die alten Claude-Branches sind fachlich erledigt; ihr Löschen ist nicht abgestimmt.
- Ob Dependabot-PRs automatisch gemergt werden dürfen, ist nicht geregelt.
