# Backlog

Stand: 9. November 2026. Die verbindliche Entwicklungsreihenfolge, Lieferpakete und
Abnahmekriterien stehen in [ROADMAP.md](ROADMAP.md), das Produktziel in
[ADR 0009](docs/adr/0009-iam-und-lizenzmanagement.md).
Dieses Backlog ergänzt die Roadmap; „eingeplant“ bedeutet noch nicht umgesetzt.

## In die Roadmap übernommen

| Thema | Phase / Paket |
|---|---|
| HTTPS, Entra-SSO, kontrollierter lokaler Notzugang | 5.1 / 5.2 (KI-1, KI-28, KI-29) |
| Externes Backup-Ziel, Verschlüsselung, Restore | 5.3 (KI-6, KI-30) |
| Zertifikatsablauf, CG-Ausnahme, Dependabot, SBOM/Image-Scan | 5.4 (KI-4, KI-13, KI-24) |
| Bestands-/Kontohistorie und Datenqualität | 6.1 / 6.2 (KI-18, KI-19, KI-26) |
| Mandantenseite, Verbindungstests, Berichtsempfänger in der Oberfläche | 6.3 (KI-25, KI-31) |
| Fortschritt beim Abruf, Sammelaktion „behalten“ | 6.4 (KI-21, KI-26) |
| Lizenzcockpit, Preise/Verträge, Optimierung, Bestands-/Kostenvergleich und Berichte | 7.1 bis 7.4 |
| Personen/Konten, Gruppen, Rollen, technische Identitäten, Verantwortliche | 8.1 bis 8.4 |
| Tool-Rollen, Anträge, Zugriffsprüfungen, Eintritt/Wechsel/Austritt | 9.1 bis 9.5 |
| Freigegebene Änderungen in Quellen | 10, ausschließlich Entscheidungsoption mit eigener Freigabe |
| Lese-API für Skripte und Integrationen, Authentifizierung gesondert entwerfen | 11 (KI-27) |
| Postgres, dauerhafte Jobs und prozessübergreifende Sperren | 11, bei belegtem Bedarf |

## Ergänzungen ohne feste Lieferzusage

- `web/review.py` auf `web/common.py` umstellen (gemeinsame Templates/Hilfen;
  reines Refactoring, eigener PR vor Erweiterungen an der Prüfliste).
- Optional TOTP für lokale Konten (KI-29), nach Festlegung des SSO-/Notzugangskonzepts.
- Mail-Bericht: HTML, Link ins Tool (`KW_PUBLIC_URL`) und optionale Verlaufsdiagramme (KI-31).
  Bestands-/Kostenvergleich gehört bereits zu Phase 7.4.
- HTMX für Entscheidungen ohne Seitenwechsel; der bestehende Jinja2-Ablauf bleibt nutzbar.
- HR-Abgleich (Excel-Upload oder Schnittstelle): seit 2. Nov. 2026 zurückgestellt;
  in Phase 9 zunächst manuelle Lebenszyklusvorgänge, Anbindung bei Bedarf in Phase 11 bewerten.
- Teams-Webhook als weiterer Benachrichtigungsweg (bisher nicht gewünscht).
- Uvicorn-Startzeilen als JSON (`--log-config`), siehe KI-2.
- Zertifikats-Login mit SHA-256-Fingerabdruck statt SHA-1 (KI-11); bei Connector-Pflege prüfen.
- Kundenportal und weitere SaaS-/Verzeichnisanbieter: eigener Anwendungsfall und Scope vor Planung.

## Bewusst verworfen

- **Rollenmodell mit Mandantensicht für Kunden:** Nur die Softchem-IT nutzt die Oberfläche
  (2. Nov. 2026). Neu bewerten, falls Kunden Zugriff bekommen sollen.
- **Schwellwert je Kontoart:** 30 Tage gelten für alles (2. Nov. 2026).
- **Unbegleitete automatische Deaktivierung oder Löschung:** weiterhin ausgeschlossen.
  Kontrollierte aktive Verwaltung ist seit ADR 0009 eine spätere Entscheidungsoption;
  Phase 10 hebt die aktuellen Schreibschutzregeln nicht auf.
- **Client-Secret statt Zertifikat für Graph:** läuft ab, liegt leichter im Klartext herum.
- **Windows-Agent mit PowerShell statt LDAPS:** mehr bewegliche Teile; nur Rückfall, falls CVH
  Port 636 nicht erreichbar macht (offene Entscheidung).
- **LDAP ohne TLS (Port 389):** Zugangsdaten und Benutzerdaten im Klartext im Netz.
- **Graph-SDK:** für diesen Umfang unnötige Abhängigkeit; msal + httpx genügen.
- **JavaScript-Build / SPA:** Jinja2 hält Image und Code klein; HTMX bei konkretem Bedarf.
