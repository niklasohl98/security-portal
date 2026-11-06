# Roadmap

Quelle: [docs/plan.md](docs/plan.md). Jede Phase endet mit grünen Tests und Abnahme durch Niklas.

| Phase | Inhalt | Status |
|---|---|---|
| 1 Grundgerüst | Repo, Docker, FastAPI, Datenbank, Mandanten aus YAML, `/health` | umgesetzt und gemergt (PR 1), CI grün |
| 2 Entra | Graph-Connector mit Zertifikat, Paging, Retry, Lizenzen | abgeschlossen am 3. Nov. 2026: Abnahme auf der Synology gegen CG und CVH (`check-entra` grün, Abruf CVH: 225 Konten, 12 SKUs) |
| 3 AD | LDAPS-Connector, DC-Erkennung, Gesundheitscheck, lastLogon über alle DCs | abgeschlossen; Abnahmelauf auf der Synology am 5. Nov. 2026 durchgeführt (CG: 8 DCs, 511 Konten; CVH: 2 DCs, 227 Konten; `lastLogon` über alle DCs mit PowerShell abgeglichen), Abnahme durch Niklas am 6. Nov. 2026 |
| 4 Logik und Oberfläche | Merge, Status, Kontoart, Empfehlung, Seiten, Ausnahmen, Export, Scheduler | abgeschlossen am 6. Nov. 2026: Abnahme durch Niklas nach Lauf auf der Synology (PR 13 bis 19: Logik, Prüfliste, Zeitplan, Seiten, Export, lokale Anmeldung, SKU-Katalog) |
| 5 Härtung und Ausbau | Entra-OIDC-Login (Softchem-Mandant), Audit-Log, Backups, Reverse-Proxy, Test auf der Synology; danach Mail-Bericht; Verlauf optional | begonnen: lokale Anmeldung mit Benutzerdatenbank und Zwangswechsel des Startpassworts fertig (6. Nov. 2026, ADR 0007), Audit-Log wird genutzt; Backups fertig (6. Nov. 2026); Benutzerverwaltung mit Admin-Rolle und Einmalpasswörtern fertig (6. Nov. 2026); Mail-Bericht per SMTP fertig (6. Nov. 2026, ADR 0008; Zugangsdaten für den Livebetrieb offen); offen: Entra-OIDC, Reverse-Proxy mit HTTPS |

Offene Entscheidungen mit Phasenbezug stehen am Ende von `docs/plan.md`.

Entschieden am 2. Nov. 2026 (Details in `docs/plan.md`): nur Softchem-IT als Nutzer, 30 Tage für
alles, Betrieb auf der Synology, Mail-Bericht ja, HR-Abgleich zurückgestellt.
Nächster Schritt (6. Nov. 2026, Reihenfolge von Niklas): Backups, Benutzerverwaltung und Mail-Bericht sind
fertig; danach strategische Ausrichtung Richtung Lizenzmanagement.
Für CG gilt bis zum Enddatum die befristete Schlüssel-Ausnahme (ADR 0005, KI-13).
Das Dienstkonto braucht in gehärteten Domänen ein Leserecht auf Benutzerobjekte (`docs/ad-setup.md`).
