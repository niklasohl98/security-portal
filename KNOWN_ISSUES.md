# Known Issues

Befunde mit Schweregrad, Workaround und Status. „Akzeptiert“ ist erlaubt, aber begründet.

| ID | Schwere | Befund | Workaround / Plan | Status |
|---|---|---|---|---|
| KI-1 | hoch | Die Oberfläche hat noch keine Anmeldung. `/health` ist offen, weitere Seiten gibt es noch nicht. | Port nur an `127.0.0.1` gebunden; Anmeldung und Proxy in Phase 5. **Vor Phase 4 (erste Kontodaten in der Oberfläche) nicht mit echten Daten betreiben**, ohne dass Phase 5 oder ein vorgeschalteter Schutz steht. | offen |
| KI-2 | niedrig | Zwei Uvicorn-Startzeilen („Started server process“, „Waiting for application startup“) erscheinen vor dem JSON-Logging als Klartext. Kein sensibler Inhalt. | `--log-config` in Backlog. | akzeptiert |
| KI-3 | niedrig | Starlette meldet eine Deprecation-Warnung für `TestClient` mit `httpx`. Nur Tests betroffen. | Beobachten, bei Starlette-Update prüfen. | offen |
| KI-4 | niedrig | Die Image-Basis ist per Digest gepinnt, es gibt aber noch keinen Schwachstellen-Scan des Images. | `pip-audit` in der CI deckt Python-Pakete; Trivy in Backlog. | offen |
| KI-5 | mittel | Die CI-Jobs (`test`, `audit`, `container`) waren nur lokal nachgestellt. | Erster Lauf auf GitHub am 2. Nov. 2026 grün (Push und PR), inklusive Image-Build mit dem unveränderten Dockerfile. | erledigt |
| KI-6 | mittel | Backups liegen zunächst nur lokal auf der Synology, also auf demselben Gerät wie die Daten. Bei Geräteausfall oder Verschlüsselungsschaden gehen Daten und Backup gemeinsam verloren. | Die Datenbank ist rekonstruierbar (jeder Lauf liest alles neu), verloren gingen Ausnahmen, Audit-Log und Verlauf. Externes Backup-Ziel in Phase 5 festlegen. | akzeptiert (befristet bis Phase 5) |
| KI-7 | mittel | LDAPS-Zertifikate der DCs sind nicht verifiziert. | Prüfung durch Niklas vor Phase 3; ohne LDAPS startet der AD-Connector nicht. | offen |
| KI-8 | niedrig | `LA-CG-GC` liefert leere Attribute und wird bei jedem Lauf als verdächtig gemeldet. | Reparatur später (`repadmin /showrepl`, `dcdiag /v`); der DC-Check schließt ihn für Stammdaten aus. | akzeptiert |
| KI-9 | mittel | Phase 2 ist nur gegen gemockte Graph-Antworten getestet. Die Annahmen zu `signInActivity` (403 ohne P1/Berechtigung) und zu Feldnamen stützen sich auf die Graph-Doku, nicht auf einen echten Mandanten. | Abnahmelauf gegen CG und CVH: `check-entra`, dann `sync-entra`; Konten- und SKU-Zahl mit dem Graph Explorer vergleichen. Am 3. Nov. 2026 auf der Synology durchgeführt: `check-entra` für beide Mandanten grün (`signInActivity` verfügbar), Abruf CVH `success` mit 225 Konten und 12 SKUs. Phase 2 durch Niklas abgenommen. | erledigt |
| KI-10 | niedrig | Die SKU-Namenstabelle (`config/sku_names.example.yaml`) ist klein und unvollständig; die Excel-Zuordnung aus der manuellen Auswertung ist noch nicht übernommen. Unbekannte SKUs erscheinen mit Teilenummer. | Eigene `config/sku_names.yaml` aus der Excel-Tabelle erstellen (Format siehe Vorlage). | offen |
| KI-11 | niedrig | MSAL stuft den Zertifikats-Login mit SHA-1-Fingerabdruck als veraltet ein (SHA-256 per `public_certificate` möglich). Entra akzeptiert ihn weiterhin. | Umstellung auf SHA-256-Fingerabdruck bei Bedarf als eigenes Ticket. | akzeptiert |
| KI-12 | niedrig | Der Entra-Abruf läuft nur per Befehlszeile, kein Zeitplan, keine Bereinigung der Rohdaten nach 90 Tagen. | Scheduler und Bereinigungsjob in Phase 4. | offen |
