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
