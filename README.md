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
| `KW_SKU_NAMES_FILE` | `/config/sku_names.yaml` | Optionale Zuordnung SKU-Teilenummer → Anzeigename (Vorlage: `config/sku_names.example.yaml`) |
| `KW_LOG_LEVEL` | `INFO` | Log-Stufe |

Die YAML ist die Quelle der Wahrheit für Mandanten und wird bei jedem Start in die Datenbank
übernommen. Mandanten, die dort fehlen, werden deaktiviert, nie gelöscht.
