# Kontenwächter

Lokales Sicherheitstool (Docker), das Benutzer aus Entra ID (Graph) und On-Prem-AD (LDAPS)
mehrerer Mandanten liest und Konten ohne Anmeldung seit > 30 Tagen markiert.
**Nur lesend. Niemals Konten in Entra oder AD ändern, deaktivieren oder löschen.**
Profil: Streng (sensible Daten, Dauerdienst). Konzept: `docs/plan.md`.

## Befehle (vor jedem Push alle lokal ausführen, sie entsprechen der CI)
- Setup: `uv venv --python 3.12 .venv && uv pip sync requirements-dev.txt`
- Format: `.venv/bin/ruff format .` (CI: `ruff format --check .`)
- Lint: `.venv/bin/ruff check .`
- Typen: `.venv/bin/mypy`
- Tests + Abdeckung: `.venv/bin/pytest --cov` (Untergrenze in `pyproject.toml`, nie senken)
- Lockfiles neu: `uv pip compile pyproject.toml --generate-hashes -o requirements.txt` und
  `... --group dev ... -o requirements-dev.txt` (je `--python-version 3.12`)
- Entra-Test/Abruf: `docker compose exec app python -m app.cli check-entra|sync-entra <kürzel>`
- Start lokal: `cp config/tenants.example.yaml config/tenants.yaml && docker compose up -d --build`
- Migration nach Modelländerung: Rezept in `docs/architecture.md` (kein `alembic.ini`); danach
  `UTCDateTime` durch `sa.DateTime(timezone=True)` ersetzen. Der Sync-Test schlägt sonst fehl.

## Stack
Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy + Alembic, SQLite, APScheduler,
msal + httpx (Graph), ldap3 (AD), openpyxl (Export), pytest, ruff, mypy.

## Struktur
`app/` (main, config, cli, db/, core/, connectors/, später export/, web/, auth/) · `tests/` ·
`config/` (nur `tenants.example.yaml` im Repo) · `docs/` (plan, architecture, adr/) ·
`ROADMAP.md`, `BACKLOG.md`, `KNOWN_ISSUES.md`, `SECURITY.md`.

## Harte Regeln
- Jede Tabelle (außer `tenant`, `sku_name`) hat `tenant_id`, jede Abfrage filtert danach.
- Geheimnisse nur als Docker-Secrets (`/run/secrets/<name>`), nie im Repo, in `.env`, DB oder Logs.
  Die DB und `tenants.yaml` speichern nur den *Namen* des Secrets.
- Letzte Anmeldung = max(Entra lastSuccessfulSignIn, Entra lastNonInteractiveSignIn,
  AD lastLogon über ALLE DCs, AD lastLogonTimestamp). `lastSignInDateTime` nur anzeigen.
- AD: DC-Gesundheitscheck vor jedem Lauf. Ein DC mit leerer userAccountControl/whenCreated ist
  defekt und wird für Stammdaten nicht genutzt.
- Zuordnung Entra ↔ AD: onPremisesImmutableId = objectGUID, dann sAMAccountName, dann UPN.
- LDAPS immer mit Zertifikatsprüfung, nie `verify=False`, nie LDAP ohne TLS.
- Zeiten immer timezone-aware (UTC); `UTCDateTime` lehnt naive Werte ab. Fristen aus der
  Mandantenkonfiguration, keine festen Zahlen in der Logik.
- Logs: nur feste Texte mit Platzhaltern, Operation/Status/Dauer/Anzahl/technische IDs. Keine
  Namen, UPNs, Mail-Adressen, Tokens. Validierungsfehler nennen Feldpfade, nie Eingabewerte.
- CSV: Semikolon, UTF-8 mit BOM, Datum `TT.MM.JJJJ HH:MM`.
- Keine echten Mandanten- oder Personendaten in Fixtures, Tests, Commits, Beispielen.
- Keine Versionen, Hashes oder API-Felder erfinden: aus Quelle ermitteln.

## Arbeitsweise
- Phasenweise nach `docs/plan.md`; nach jeder Phase stoppen und berichten.
- Dateien ≤ ca. 400 Zeilen, Funktionen ≤ ca. 80. Öffentliche Schnittstellen mit Docstring.
- Verhaltensänderung und Refactoring nie im selben PR. Commits: `feat|fix|docs|refactor|test|chore|ci(scope): Text`.
- Doku (README, architecture, CHANGELOG, ADR, KNOWN_ISSUES) gehört in denselben PR wie die Änderung.
- Tests mit gemockten Graph-/LDAP-Antworten (respx, ldap3-Mock); Fixtures anonymisiert.
- Sandbox-Hinweis: Docker-Build scheitert hier am Proxy-CA (siehe `CONTRIBUTING.md`).
