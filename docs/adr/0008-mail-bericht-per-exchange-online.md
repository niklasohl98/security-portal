# 0008 – Mail-Bericht über Exchange Online (Graph sendMail), nur Kennzahlen

- **Status:** Accepted (2026-11-06)

## Kontext
Der Plan sieht einen Wochenbericht je Mandant per Mail vor. Offen war der Weg (SMTP-Relay oder Graph
`sendMail`). Niklas hat Exchange Online über Microsoft Graph gewählt, aus einem **zusätzlichen
Softchem-Tenant**; über denselben Tenant soll später auch die Anmeldung per SSO laufen. Die Mail
verlässt das Tool und landet in Postfächern, der Bericht darf also nichts enthalten, was dort nicht
hingehört.

## Entscheidung
- **Weg:** `POST /users/{postfach}/sendMail` an Microsoft Graph (Anwendungsberechtigung `Mail.Send`),
  Antwort 202. Keine neue Abhängigkeit: `httpx` und `msal` sind schon da.
- **Eigene App-Registrierung im Softchem-Tenant**, getrennt von den lesenden Zugängen zu den
  Kunden-Tenants. `connectors/graph.py` bleibt rein lesend; der Versand liegt in `mail/transport.py`
  und nutzt nur die Token-Klasse (`MsalCertificateTokenProvider`) mit. Anmeldung per **Zertifikat**
  (privater Schlüssel als Docker-Secret, in der Umgebung nur dessen Name).
- **Berechtigung auf ein Postfach begrenzen.** `Mail.Send` als Entra-Anwendungsberechtigung gilt für
  *alle* Postfächer des Tenants. Deshalb wird sie nicht in Entra erteilt, sondern in Exchange Online
  per **RBAC for Applications** (`Application Mail.Send` mit einem Management Scope auf das eine
  Absenderpostfach). Das ist nach Microsofts Dokumentation der Ersatz für Application Access
  Policies; Entra-Zustimmungen und Exchange-Rollen addieren sich, eine zusätzliche unbegrenzte
  Zustimmung in Entra würde die Begrenzung aufheben (`docs/mail-setup.md`).
- **Inhalt:** nur Kennzahlen (Konten, markierte Konten, freiwerdende Lizenzen je Produkt, offene
  Prüfungen, Ausnahmen, Stand der Abrufe, Warnungen). **Keine Konten**: keine Namen, UPNs, Adressen.
  Wer Einzelheiten braucht, öffnet das Tool.
- **Empfänger** je Mandant in der YAML (`report.recipients`, höchstens 20, Format geprüft) und als
  Spalte `tenant.report_recipients`. Ohne Empfänger gibt es keinen Bericht für den Mandanten.
- **Zeitplan:** wöchentlich (`KW_REPORT_WEEKDAY`, `KW_REPORT_TIME`, Standard Montag 07:30), nur wenn
  `KW_MAIL_FROM` gesetzt ist. Ein Fehler bei einem Mandanten stoppt die anderen nicht.
- **Keine automatische Wiederholung:** `sendMail` ist nicht idempotent; eine Wiederholung nach einem
  unklaren Fehler könnte doppelt senden. Fehler werden protokolliert, „Jetzt senden“ wiederholt.
- **Fehler und Protokolle:** `MailError` trägt einen festen Code (`secret`, `auth`, `forbidden`,
  `sender`, `rejected`, `throttled`, `connect`, `send`) und höchstens den technischen Fehlercode von
  Graph (z. B. `ErrorAccessDenied`, auf `[A-Za-z0-9_.:-]` begrenzt). Meldungstexte und Originalfehler
  werden verworfen, weil sie Adressen enthalten können. Audit-Log: `report.sent` mit
  `recipients=<Zahl>`, `report.failed` mit `error=<Code> [graph=<Code>]`, nie Adressen.
- **SSO später:** Die Mail-App ist eine reine Anwendungs-App (kein Benutzer-Login). Das SSO bekommt
  eigene Einstellungen und, falls gewünscht, eine eigene App-Registrierung im selben Tenant;
  `KW_MAIL_*` bleibt davon unberührt.

## Folgen
- Der Bericht ist eine reine Textmail ohne HTML, Anhänge und Wochenvergleich (Backlog).
- „Anmeldung prüfen“ holt nur ein Token. Ob das Postfach senden darf, zeigt erst der Versand;
  `Test-ServicePrincipalAuthorization` in Exchange prüft es vorab (`docs/mail-setup.md`).
- Änderungen an Exchange-Berechtigungen wirken laut Microsoft nach 30 Minuten bis 2 Stunden.
- Empfängeradressen stehen in `tenants.yaml`, in der Datenbank und in den Sicherungen.
