# Roadmap

Quelle: [docs/plan.md](docs/plan.md). Jede Phase endet mit grünen Tests und Abnahme durch Niklas.

| Phase | Inhalt | Status |
|---|---|---|
| 1 Grundgerüst | Repo, Docker, FastAPI, Datenbank, Mandanten aus YAML, `/health` | umgesetzt, Abnahme offen |
| 2 Entra | Graph-Connector mit Zertifikat, Paging, Retry, Lizenzen | offen |
| 3 AD | LDAPS-Connector, DC-Erkennung, Gesundheitscheck, lastLogon über alle DCs | offen |
| 4 Logik und Oberfläche | Merge, Status, Kontoart, Empfehlung, Seiten, Ausnahmen, Export, Scheduler | offen |
| 5 Härtung und Ausbau | Entra-OIDC-Login, Audit-Log, Backups, Reverse-Proxy; optional Mail, HR, Verlauf | offen |

Offene Entscheidungen mit Phasenbezug stehen am Ende von `docs/plan.md`.
