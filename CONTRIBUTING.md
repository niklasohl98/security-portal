# Mitarbeit

## Ablauf
- `main` ist immer lauffähig. Arbeit auf Branches, ein Thema je PR. Gemergt wird per Merge-Commit,
  damit getrennte Commits (z. B. ein Fix vor einem Feature) erhalten bleiben. Von Claude geöffnete
  PRs werden bei grüner CI automatisch gemergt (Freigabe von Niklas, siehe `CLAUDE.md`).
- Commits nach Conventional Commits, Deutsch: `feat|fix|docs|refactor|test|chore|ci(scope): Beschreibung`.
- Verhaltensänderung und Refactoring nie im selben PR.
- Vor dem Push dieselben Befehle wie die CI (siehe `CLAUDE.md`): `ruff format --check .`,
  `ruff check .`, `mypy`, `pytest --cov`.
- Definition of Done: siehe `.github/PULL_REQUEST_TEMPLATE.md`.

## Regeln
- Nur lesender Zugriff auf Entra und AD. Schreibzugriffe sind ausgeschlossen.
- Keine Secrets, Mandanten- oder Personendaten in Code, Tests, Fixtures, Logs oder Commits.
- Neue Abhängigkeit nur mit Begründung im PR; danach Lockfiles neu erzeugen (Befehle in `CLAUDE.md`).
- Entscheidungen mit großer Tragweite: ADR unter `docs/adr/` (nächste freie Nummer, vorher `main`
  abgleichen). ADRs werden nicht gelöscht, sondern durch neue ersetzt.
- Neue Konfiguration gehört in `config/tenants.example.yaml`, `.env.example` und die Doku.

## Docker-Build in der Cloud-Sandbox
Der Egress-Proxy der Sandbox terminiert TLS mit eigenem CA. Der Build scheitert dort bei
`pip install`, ohne dass das Dockerfile falsch ist. Zum lokalen Testen eine temporäre Kopie des
Build-Kontexts mit `COPY ca.crt` und `ENV PIP_CERT=...` bauen, das Produktions-Dockerfile bleibt
unverändert, und danach `docker compose up -d --no-build`. `dockerd` muss in der Sandbox selbst
gestartet werden.
