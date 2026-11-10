# Sicherheit

Das Tool hält eine vollständige Benutzerliste mehrerer Kunden mit Anmeldeverhalten und ist damit
selbst ein lohnendes Ziel.

## Schwachstellen melden

Bitte **nicht** als öffentliches Issue, sondern direkt an die Betreiber (Niklas, siehe
Repository-Profil). Keine echten Mandanten- oder Personendaten in Meldungen.

Eine Meldung nennt betroffene Version, reproduzierbare Schritte, erwartete Auswirkung und eine
Kontaktmöglichkeit. Beweise verwenden ausschließlich erfundene Daten.

## Schutzgüter und Grenzen

Die SQLite-Datenbank und ihre Sicherungen enthalten Kontenbestände, Anmeldezeiten, Lizenzen,
Ausnahmen, lokale Passwort-Hashes und Audit-Ereignisse. Private Konfiguration und Docker-Secrets
enthalten Zugangsdaten zu Quellen, Mail, SSO und externem Backup. Zugriff auf Host, DSM,
Backup-Ziel und CI-Artefakte ist deshalb auf die zuständigen Administratoren zu begrenzen.

Der Browser vertraut dem HTTPS-Reverse-Proxy; die App vertraut weitergeleiteten Angaben nur von
explizit konfigurierten Proxy-IPs. Entra und AD sind lesende Kundendatenquellen. Der separate
Mail-Zugang darf ausschließlich aus dem vorgesehenen Absenderpostfach senden. Externe Sicherungen
sind ein eigenes Vertrauensgebiet und benötigen einen unabhängigen Notfallzugang.

## Technische Grundsätze

- Nur Leserechte an Entra (App-Berechtigungen `User.Read.All`, `AuditLog.Read.All`,
  `Organization.Read.All`; `Group.Read.All` ist nicht nötig) und AD (Dienstkonto nur in „Domain Users“,
  interaktive Anmeldung per GPO verboten).
- Zertifikats-Login statt Client-Secret; private Schlüssel und Passwörter nur als Docker-Secrets.
- LDAPS mit Zertifikatsprüfung; kein LDAP ohne TLS.
- Logs ohne Namen, UPNs, Tokens oder Passwörter; Validierungsfehler ohne Eingabewerte.
- Audit-Details enthalten technische IDs, Zahlen und Fehlercodes; der vollständige Ereigniskatalog
  steht in [docs/audit-log.md](docs/audit-log.md).
- Der Graph-Client sendet nur `GET`, folgt `@odata.nextLink` nur auf `graph.microsoft.com` und
  loggt weder Antwortinhalte noch Fehlertexte, nur Statuscode und Fehlercode.
- Container: Nicht-root (UID 10001), schreibgeschütztes Dateisystem, keine Linux-Capabilities.
- Zugriff auf die Oberfläche nur mit lokaler Anmeldung oder optionalem Entra-SSO. Für den
  Livebetrieb ist HTTPS über den vorbereiteten DSM-Reverse-Proxy vorgesehen; dessen konkrete
  Einrichtung und Abnahme bleibt bis zum Deployment als KI-1 offen.
- Security-Review vor dem ersten Einsatz mit echten Daten; Befunde in `KNOWN_ISSUES.md`.

## Web- und Zugangsschutz

Lokale Passwörter werden mit Argon2id gehasht. Anmeldefehler führen zu zeitweiser Kontosperre;
Sitzungen liegen serverseitig, Cookies sind `HttpOnly`, `SameSite=Lax` und bei öffentlicher
HTTPS-Origin `Secure`. Zustandsändernde Formulare verlangen Same-Origin und ein sitzungsgebundenes
CSRF-Token. Entra-SSO verwendet Authorization Code Flow mit PKCE, Browserbindung, geprüfte
JWT-Signaturen und periodische Gruppenprüfung. Tokens werden nicht persistiert. Ein lokaler Admin
bleibt als kontrollierter Notzugang erhalten.

## Entwicklung und Betrieb

CI prüft statische Qualität, Typen, Tests, Python-Abhängigkeiten, Containerbetrieb, SBOM und
Container-Schwachstellen. Behebbare hohe oder kritische Image-Befunde blockieren. Das
[Betriebshandbuch](docs/operations.md) beschreibt Überwachung, Updates, Wiederherstellung und
offene Live-Abnahmen. Bekannte akzeptierte oder offene Risiken stehen mit Maßnahme und Status in
[KNOWN_ISSUES.md](KNOWN_ISSUES.md).
