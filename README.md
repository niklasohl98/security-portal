# Kontenwächter

Liest täglich alle Benutzer aus **Entra ID** und dem **On-Prem-AD** mehrerer Mandanten (zunächst
CG und CVH), führt sie pro Person zusammen und markiert Konten ohne Anmeldung seit mehr als
30 Tagen. Ergebnis: eine filterbare Liste im Browser, exportierbar als CSV und XLSX.
**Das Tool liest nur.** Es ändert, deaktiviert oder löscht keine Konten.

## Status

Phase 1 von 5 (Grundgerüst) ist umgesetzt: Container, FastAPI mit `/health`, Datenbankmodelle mit
Alembic, Mandanten aus YAML. Die Anbindung an Entra (Phase 2) und AD (Phase 3), die Regeln und die
Oberfläche (Phase 4) sowie Anmeldung und Reverse-Proxy (Phase 5) fehlen noch. **Noch nicht für den
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

`config/tenants.yaml` steht in `.gitignore`. Dort stehen nur *Namen* von Secrets; die Werte
(Zertifikats-Schlüssel, AD-Passwörter) kommen ab Phase 2 als Docker-Secrets nach `/run/secrets/`.

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
| `KW_LOG_LEVEL` | `INFO` | Log-Stufe |

Die YAML ist die Quelle der Wahrheit für Mandanten und wird bei jedem Start in die Datenbank
übernommen. Mandanten, die dort fehlen, werden deaktiviert, nie gelöscht.
