# Backlog

## Später (Phase 5 und danach)
- Benutzerverwaltung in der Oberfläche (anlegen, deaktivieren, Passwort zurücksetzen), optional TOTP als zweiter Faktor (KI-29)
- `GET /api/accounts` mit API-Token für Skripte (Plan, Export); zusammen mit der Anmeldung (KI-27)
- Oberfläche: Mandantenseite (anlegen, bearbeiten, Verbindung testen), Verlauf je Konto, Fortschritt von „Jetzt abrufen“ (KI-25, KI-26)
- `web/review.py` auf `web/common.py` umstellen (gemeinsame Templates und Hilfen; reines Refactoring, eigener PR)
- Prüfliste: Sammelaktion „alle behalten“ je Mandant (KI-21); HTMX für Entscheidungen ohne Seitenwechsel
- Mail-Bericht je Mandant (gewünscht, Phase 5; SMTP-Weg offen), Verlaufsdiagramme (optional)
- HR-Abgleich (Excel-Upload): **zurückgestellt** am 2. Nov. 2026, bei Bedarf neu bewerten
- Teams-Webhook als weiterer Benachrichtigungsweg (nicht gewünscht, nur falls Mail nicht reicht)
- SBOM und Image-Schwachstellen-Scan in der CI (Trivy o. ä., Action per Commit-Hash pinnen)
- Uvicorn-Startzeilen als JSON (`--log-config`), siehe KNOWN_ISSUES
- Umstellung SQLite → Postgres, falls mehr Mandanten oder parallele Schreiber nötig werden
- Mandanten über die Oberfläche anlegen (bisher nur YAML)
- Zertifikatsablauf aus dem Zertifikat lesen und in der Übersicht 30 Tage vorher warnen (Phase 4);
  dafür den öffentlichen Teil des Zertifikats mit ablegen
- Zertifikats-Login mit SHA-256-Fingerabdruck statt SHA-1 (KI-11)

## Bewusst verworfen
- **Rollenmodell mit Mandantensicht für Kunden:** Nur die Softchem-IT nutzt die Oberfläche
  (2. Nov. 2026). Neu bewerten, falls Kunden Zugriff bekommen sollen.
- **Schwellwert je Kontoart:** 30 Tage gelten für alles (2. Nov. 2026).
- **Konten automatisch deaktivieren oder löschen:** Das Tool ist ein Wächter, kein Akteur; ein
  Schreibzugriff macht es zum Angriffsziel mit Schadenspotenzial und braucht Freigabeprozesse.
- **Client-Secret statt Zertifikat für Graph:** läuft ab, liegt leichter im Klartext herum.
- **Windows-Agent mit PowerShell statt LDAPS:** mehr bewegliche Teile; nur Rückfall, falls CVH
  Port 636 nicht erreichbar macht (offene Entscheidung).
- **LDAP ohne TLS (Port 389):** Zugangsdaten und Benutzerdaten im Klartext im Netz.
- **Graph-SDK:** für diesen Umfang unnötige Abhängigkeit; msal + httpx genügen.
- **JavaScript-Build / SPA:** Jinja2 + HTMX hält Image und Code klein.
