# Mitarbeit

## Ablauf
- `main` ist immer lauffähig. Arbeit auf Branches, ein Thema je PR. Gemergt wird per Merge-Commit,
  damit getrennte Commits (z. B. ein Fix vor einem Feature) erhalten bleiben. Eigene PRs werden
  nur nach der im jeweiligen Arbeitsauftrag geltenden Freigabe und mit grüner CI zusammengeführt.
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
- Neue Funktionen erfüllen den [Dokumentationsstandard](docs/documentation.md). Die kanonischen
  Referenzen für Umgebungsvariablen und Befehle sind `docs/configuration.md` und `docs/cli.md`.

## Docker-Build in der Cloud-Sandbox
Der Egress-Proxy der Sandbox terminiert TLS mit eigener CA. Der Dockerfile akzeptiert ein
optionales Build-Secret, das ausschließlich während der Netzwerk-Schritte verfügbar ist:

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t kontenwaechter:dev .
```

Keine Session-CA in Image-Layer kopieren und keine TLS-Prüfung abschalten. Im normalen Build
wird kein Secret benötigt. In der verwalteten Cloud-Umgebung wird Docker bereitgestellt;
den lokalen Daemon gemäß Runtime-Skill prüfen, nicht ungeprüft einen zweiten starten.
Ohne Docker kann der Python-Testlauf erfolgen; für den echten Restic-Roundtrip das gepinnte
Binary aus dem Build bereitstellen (`KW_TEST_RESTIC_BINARY`). CI baut hierfür das Ziel `restic`.

Restic wird wegen gemeldeter Sicherheitslücken des offiziellen Binärstands aus geprüftem
Upstream-Modul mit aktualisierten Abhängigkeiten gebaut. Go-Lockfiles und Compiler sind in
`docker/restic/` bzw. im Dockerfile gesperrt; Wartung/Prüfung siehe dortige README.
