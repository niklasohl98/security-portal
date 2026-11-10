# Entwicklungs- und Testhandbuch

## Lokale Umgebung

Voraussetzungen sind Python 3.12, `uv`, Git und für den vollständigen Lauf Docker mit Compose.

```bash
uv venv --python 3.12 .venv
UV_CACHE_DIR=/tmp/kontenwaechter-uv-cache uv pip sync requirements-dev.txt
cp config/tenants.example.yaml config/tenants.yaml
```

Fixtures und Beispielkonfiguration enthalten ausschließlich synthetische Daten. Reale Mandanten-
oder Personendaten sowie Secrets dürfen nie in Tests, Snapshots, Logs oder Commits gelangen.

## Codekarte

| Bereich | Verantwortung |
|---|---|
| `app/config.py` | Prozess-/Mandantenkonfiguration, strikte Validierung, Secret-Dateien |
| `app/connectors/` | externe Entra-/AD-Lesezugriffe und Protokollgrenzen |
| `app/core/` | fachliche Regeln, Zusammenführung, Läufe, Berichte, Sicherung, Scheduler |
| `app/auth/` | lokale Anmeldung, SSO, Sitzungen und Benutzerverwaltung |
| `app/backup/` | externes Paket, Restic-Prozess und Zielstatus |
| `app/mail/` | Exchange-Mailkonfiguration und `sendMail`-Transport |
| `app/db/` | SQLAlchemy-Modelle, SQLite-Engine und Alembic-Migrationen |
| `app/web/` | FastAPI-Routen, Security-Middleware, Jinja-Seiten und Filter |
| `app/export/` | gemeinsame Spalten sowie CSV-/XLSX-Erzeugung |
| `app/data/` | versionierter Microsoft-SKU-Katalog |
| `tests/` | Unit-, Integrations-, Web-, Migrations-, Sicherheits- und Doku-Regressionstests |

Der erlaubte Abhängigkeitsfluss ist im Kern: Web/CLI → Core/Auth → Connector/DB. Connectoren
ändern keine Quellen. Fachlogik gehört nicht in Templates oder Route-Handler. Öffentliche Klassen
und Funktionen auf Modulebene benötigen einen Docstring.

## Qualitätsbefehle

Vor jedem Push laufen dieselben vier Prüfungen wie in CI:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest --cov
```

`mypy` ist strikt. Coverage misst Branches, lässt Migrationen aus und darf gegenüber dem Stand vor
der Änderung nicht sinken; die technische Untergrenze in `pyproject.toml` ist 95 %. Tests sollen
Verträge, Fehlerfälle und Sicherheitsgrenzen prüfen, nicht lediglich Implementierungszeilen
nachzeichnen.

## Testaufbau

- `tests/conftest.py` erstellt pro Test eine temporäre SQLite-Datenbank und synthetische Mandanten.
- Graph wird mit `respx`/Fakes, LDAP mit `ldap3`-Mock/Fakes geprüft; Tests greifen nicht auf echte
  Kundenquellen zu.
- Webtests verwenden `TestClient`, echte Middleware, Sitzungen und CSRF-Token.
- Modell-/Migrationstests vergleichen das erzeugte Schema mit SQLAlchemy.
- Backup-Integration verwendet ein echtes Restic-Binary, wenn `KW_TEST_RESTIC_BINARY` gesetzt oder
  `restic` im Pfad ist; CI extrahiert das Binary aus dem gepinnten Docker-Build.
- Dokumentationstests prüfen Links, Konfiguration, CLI, Routen, Tabellen, Audit-Ereignisse und
  öffentliche Docstrings gegen den Code.

Für einen einzelnen Bereich zuerst gezielt laufen lassen, anschließend immer die Gesamtsuite:

```bash
.venv/bin/pytest -q tests/test_sso.py tests/test_sso_provider.py
.venv/bin/pytest --cov
```

## Container und CI

CI hat drei unabhängige Jobs:

1. Format, Lint, Typen und Tests einschließlich echtem Restic-Roundtrip;
2. `pip-audit` gegen die gehashten Python-Lockfiles;
3. Image-Build und Smoke-Test als UID 10001 mit schreibgeschütztem Dateisystem, Neustart,
   CycloneDX-SBOM und Trivy-Gate für behebbare HIGH-/CRITICAL-Befunde.

Der Cloud-Build kann die Session-CA ausschließlich als Build-Secret erhalten; sie darf nie in ein
Image-Layer kopiert und TLS darf nie abgeschaltet werden. Details: [CONTRIBUTING.md](../CONTRIBUTING.md)
und [Restic-Provenienz](../docker/restic/README.md).

## Änderungstypen

- Verhalten und Refactoring bleiben in getrennten PRs.
- Datenmodelländerung benötigt Modell, neue Migration, Schema-/Regressionstest und
  [Datenmodellreferenz](data-model.md).
- Konfiguration, Route, CLI oder Audit-Ereignis benötigt die jeweilige Referenz und besteht die
  automatischen Doku-Prüfungen.
- Tragende Architektur-/Produktentscheidung benötigt eine ADR; neues Risiko einen Eintrag in
  `KNOWN_ISSUES.md`.
- Lockfiles werden mit den Befehlen aus `CLAUDE.md` reproduzierbar und mit Hashes erzeugt.

Definition of Done und Schreibregeln: [Dokumentationsstandard](documentation.md) und
[`PULL_REQUEST_TEMPLATE.md`](../.github/PULL_REQUEST_TEMPLATE.md).
