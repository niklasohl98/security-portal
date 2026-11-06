# Roadmap

Quelle: [docs/plan.md](docs/plan.md). Jede Phase endet mit grünen Tests und Abnahme durch Niklas.

| Phase | Inhalt | Status |
|---|---|---|
| 1 Grundgerüst | Repo, Docker, FastAPI, Datenbank, Mandanten aus YAML, `/health` | umgesetzt und gemergt (PR 1), CI grün |
| 2 Entra | Graph-Connector mit Zertifikat, Paging, Retry, Lizenzen | abgeschlossen am 3. Nov. 2026: Abnahme auf der Synology gegen CG und CVH (`check-entra` grün, Abruf CVH: 225 Konten, 12 SKUs) |
| 3 AD | LDAPS-Connector, DC-Erkennung, Gesundheitscheck, lastLogon über alle DCs | abgeschlossen; Abnahmelauf auf der Synology am 5. Nov. 2026 durchgeführt (CG: 8 DCs, 511 Konten; CVH: 2 DCs, 227 Konten; `lastLogon` über alle DCs mit PowerShell abgeglichen), Abnahme durch Niklas am 6. Nov. 2026 |
| 4 Logik und Oberfläche | Merge, Status, Kontoart, Empfehlung, Seiten, Ausnahmen, Export, Scheduler | in Arbeit: Etappe 1 (Logik, PR 13) gemergt; Etappe 2a (Prüfliste verschwundener Konten, ADR 0006) fertig; offen: 2b Ausnahmen, Scheduler, Bereinigung; 3 Seiten; 4 Export |
| 5 Härtung und Ausbau | Entra-OIDC-Login (Softchem-Mandant), Audit-Log, Backups, Reverse-Proxy, Test auf der Synology; danach Mail-Bericht; Verlauf optional | offen |

Offene Entscheidungen mit Phasenbezug stehen am Ende von `docs/plan.md`.

Entschieden am 2. Nov. 2026 (Details in `docs/plan.md`): nur Softchem-IT als Nutzer, 30 Tage für
alles, Betrieb auf der Synology, Mail-Bericht ja, HR-Abgleich zurückgestellt.
Nächster Schritt: Phase 4. Offene Punkte aus der Phase-3-Abnahme (KI-14: GUID-Byte-Reihenfolge,
`memberOf`, Paging, `null`-Werte) werden dort beim Abgleich mit Entra geprüft.
Für CG gilt bis zum Enddatum die befristete Schlüssel-Ausnahme (ADR 0005, KI-13).
Das Dienstkonto braucht in gehärteten Domänen ein Leserecht auf Benutzerobjekte (`docs/ad-setup.md`).
