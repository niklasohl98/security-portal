# 0008 – Mail-Bericht per SMTP, nur Kennzahlen

- **Status:** Accepted (2026-11-06)

## Kontext
Der Plan sieht einen Wochenbericht je Mandant per Mail vor. Offen war der Weg: SMTP-Relay oder
Graph `sendMail` aus dem Softchem-Mandanten (`docs/plan.md`, offene Fragen). Die Mail verlässt das
Tool und landet in Postfächern; der Bericht darf deshalb nichts enthalten, was dort nicht hingehört.

## Entscheidung
- **SMTP** mit der Standardbibliothek (`smtplib`), keine neue Abhängigkeit. Graph `sendMail` bräuchte
  eine weitere App-Registrierung mit der Anwendungsberechtigung `Mail.Send`; die erlaubt, als jeder
  Benutzer des Mandanten Mails zu senden, und widerspricht dem Grundsatz, nur das Nötigste zu
  berechtigen. Ein SMTP-Relay im internen Netz (Synology MailPlus, Exchange-Connector, Smarthost)
  braucht nur ein Postfach oder eine Freigabe für die IP.
- **Immer verschlüsselt:** `starttls` (Standard, Port 587) oder `ssl` (Port 465); Zertifikat und
  Hostname werden immer geprüft, eine eigene CA ist über `KW_SMTP_CA_FILE` möglich. Unverschlüsseltes
  SMTP und ein Abschalten der Prüfung gibt es nicht (gleiche Linie wie LDAPS).
- **Passwort** nur als Docker-Secret (`KW_SMTP_PASSWORD_SECRET` nennt den Namen). Benutzer und Secret
  gehören zusammen; ohne beides läuft das Relay anonym.
- **Inhalt:** nur Kennzahlen (Konten, markierte Konten, freiwerdende Lizenzen je Produkt, offene
  Prüfungen, Ausnahmen, Stand der Abrufe, Warnungen). **Keine Konten**: keine Namen, UPNs, Adressen.
  Wer Einzelheiten braucht, öffnet das Tool.
- **Empfänger** je Mandant in der YAML (`report.recipients`, höchstens 20, Format geprüft) und als
  Spalte `tenant.report_recipients`. Ohne Empfänger gibt es keinen Bericht für den Mandanten.
- **Zeitplan:** wöchentlich (`KW_REPORT_WEEKDAY`, `KW_REPORT_TIME`, Standard Montag 07:30), nur wenn
  `KW_SMTP_HOST` gesetzt ist. Ein Fehler bei einem Mandanten stoppt die anderen nicht.
- **Fehler und Protokolle:** `MailError` trägt nur einen festen Code (`secret`, `tls`, `connect`,
  `auth`, `recipients`, `send`); smtplib-Texte (können Adressen und Serverantworten enthalten)
  werden nicht weitergereicht. Audit-Log: `report.sent` mit `recipients=<Zahl>`, `report.failed` mit
  `error=<Code>`, nie Adressen.

## Folgen
- Der Bericht ist eine reine Textmail; HTML, Anhänge und Vergleich mit der Vorwoche fehlen (Backlog).
- Die Empfängeradressen stehen in `tenants.yaml` und in der Datenbank (und damit in den Sicherungen).
- Wer die Mail abfängt, sieht Zahlen und Produktnamen, aber keine Personen. Der Mailweg selbst ist
  verschlüsselt, die Zustellung im Postfach liegt außerhalb des Tools.
