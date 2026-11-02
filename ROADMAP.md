# Roadmap

Quelle: [docs/plan.md](docs/plan.md). Jede Phase endet mit grünen Tests und Abnahme durch Niklas.

| Phase | Inhalt | Status |
|---|---|---|
| 1 Grundgerüst | Repo, Docker, FastAPI, Datenbank, Mandanten aus YAML, `/health` | umgesetzt und gemergt (PR 1), CI grün |
| 2 Entra | Graph-Connector mit Zertifikat, Paging, Retry, Lizenzen | umgesetzt (Tests mit Mocks grün), Abnahmelauf gegen CG offen |
| 3 AD | LDAPS-Connector, DC-Erkennung, Gesundheitscheck, lastLogon über alle DCs | offen |
| 4 Logik und Oberfläche | Merge, Status, Kontoart, Empfehlung, Seiten, Ausnahmen, Export, Scheduler | offen |
| 5 Härtung und Ausbau | Entra-OIDC-Login (Softchem-Mandant), Audit-Log, Backups, Reverse-Proxy, Test auf der Synology; danach Mail-Bericht; Verlauf optional | offen |

Offene Entscheidungen mit Phasenbezug stehen am Ende von `docs/plan.md`.

Entschieden am 2. Nov. 2026 (Details in `docs/plan.md`): nur Softchem-IT als Nutzer, 30 Tage für
alles, Betrieb auf der Synology, Mail-Bericht ja, HR-Abgleich zurückgestellt.
Nächster Schritt: Abnahmelauf Phase 2 gegen CG (Anleitung: `docs/entra-setup.md`), danach Phase 3.
Für Phase 3 muss die LDAPS-Prüfung der DCs vorliegen.
