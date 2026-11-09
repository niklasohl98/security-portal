# Sicherheit

Das Tool hält eine vollständige Benutzerliste mehrerer Kunden mit Anmeldeverhalten und ist damit
selbst ein lohnendes Ziel.

## Schwachstellen melden
Bitte **nicht** als öffentliches Issue, sondern direkt an die Betreiber (Niklas, siehe
Repository-Profil). Keine echten Mandanten- oder Personendaten in Meldungen.

## Grundsätze
- Nur Leserechte an Entra (App-Berechtigungen `User.Read.All`, `AuditLog.Read.All`,
  `Organization.Read.All`; `Group.Read.All` ist nicht nötig) und AD (Dienstkonto nur in „Domain Users“,
  interaktive Anmeldung per GPO verboten).
- Zertifikats-Login statt Client-Secret; private Schlüssel und Passwörter nur als Docker-Secrets.
- LDAPS mit Zertifikatsprüfung; kein LDAP ohne TLS.
- Logs ohne Namen, UPNs, Tokens oder Passwörter; Validierungsfehler ohne Eingabewerte.
- Der Graph-Client sendet nur `GET`, folgt `@odata.nextLink` nur auf `graph.microsoft.com` und
  loggt weder Antwortinhalte noch Fehlertexte, nur Statuscode und Fehlercode.
- Container: Nicht-root (UID 10001), schreibgeschütztes Dateisystem, keine Linux-Capabilities.
- Zugriff auf die Oberfläche nur mit lokaler Anmeldung oder optionalem Entra-SSO. Für den
  Livebetrieb ist HTTPS über den vorbereiteten DSM-Reverse-Proxy vorgesehen; dessen konkrete
  Einrichtung und Abnahme bleibt bis zum Deployment als KI-1 offen.
- Security-Review vor dem ersten Einsatz mit echten Daten; Befunde in `KNOWN_ISSUES.md`.
