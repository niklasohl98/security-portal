# Known Issues

Befunde mit Schweregrad, Workaround und Status. „Akzeptiert“ ist erlaubt, aber begründet.

| ID | Schwere | Befund | Workaround / Plan | Status |
|---|---|---|---|---|
| KI-1 | hoch | Die Oberfläche hat noch keine Anmeldung. `/health` ist offen, weitere Seiten gibt es noch nicht. | Port nur an `127.0.0.1` gebunden; Anmeldung und Proxy in Phase 5. **Vor Phase 4 (erste Kontodaten in der Oberfläche) nicht mit echten Daten betreiben**, ohne dass Phase 5 oder ein vorgeschalteter Schutz steht. | offen |
| KI-2 | niedrig | Zwei Uvicorn-Startzeilen („Started server process“, „Waiting for application startup“) erscheinen vor dem JSON-Logging als Klartext. Kein sensibler Inhalt. | `--log-config` in Backlog. | akzeptiert |
| KI-3 | niedrig | Starlette meldet eine Deprecation-Warnung für `TestClient` mit `httpx`. Nur Tests betroffen. | Beobachten, bei Starlette-Update prüfen. | offen |
| KI-4 | niedrig | Die Image-Basis ist per Digest gepinnt, es gibt aber noch keinen Schwachstellen-Scan des Images. | `pip-audit` in der CI deckt Python-Pakete; Trivy in Backlog. | offen |
| KI-5 | mittel | Die CI-Jobs (`test`, `audit`, `container`) wurden lokal nachgestellt, aber noch nicht auf GitHub ausgeführt. | Erster Lauf nach dem Push prüfen. | offen |
