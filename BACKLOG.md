# Backlog

## Später (Phase 5 und danach)
- Mail- oder Teams-Bericht je Mandant, HR-Abgleich (Excel-Upload), Verlaufsdiagramme
- Rollenmodell: Sicht nur auf den eigenen Mandanten für Kunden-Ansprechpartner
- SBOM und Image-Schwachstellen-Scan in der CI (Trivy o. ä., Action per Commit-Hash pinnen)
- Uvicorn-Startzeilen als JSON (`--log-config`), siehe KNOWN_ISSUES
- Umstellung SQLite → Postgres, falls mehr Mandanten oder parallele Schreiber nötig werden
- Mandanten über die Oberfläche anlegen (bisher nur YAML)

## Bewusst verworfen
- **Konten automatisch deaktivieren oder löschen:** Das Tool ist ein Wächter, kein Akteur; ein
  Schreibzugriff macht es zum Angriffsziel mit Schadenspotenzial und braucht Freigabeprozesse.
- **Client-Secret statt Zertifikat für Graph:** läuft ab, liegt leichter im Klartext herum.
- **Windows-Agent mit PowerShell statt LDAPS:** mehr bewegliche Teile; nur Rückfall, falls CVH
  Port 636 nicht erreichbar macht (offene Entscheidung).
- **LDAP ohne TLS (Port 389):** Zugangsdaten und Benutzerdaten im Klartext im Netz.
- **Graph-SDK:** für diesen Umfang unnötige Abhängigkeit; msal + httpx genügen.
- **JavaScript-Build / SPA:** Jinja2 + HTMX hält Image und Code klein.
