# Audit-Log

Das Audit-Log beantwortet „wer hat wann welche lokale Aktion ausgelöst?“. Es ist kein Debug-Log und
enthält bewusst keine Kontoinhalte. Die Anwendung bietet derzeit keine eigene Audit-Seite; Einträge
liegen in `audit_log` und werden durch Datenbanksicherung/Restore erhalten.

## Felder und Datenschutz

| Feld | Inhalt |
|---|---|
| `tenant_id` | betroffener Mandant oder leer bei globalen Aktionen |
| `at` | timezone-aware UTC-Zeitpunkt |
| `actor` | lokaler Benutzername, `system`, `cli` oder bei fehlgeschlagener Anmeldung nur `user:<id>` |
| `action` | stabiler technischer Ereigniscode |
| `detail` | feste Schlüssel/Werte mit technischen IDs, Zählern und Fehlercodes |

Nicht zulässig sind Anzeigenamen, UPNs, Mailadressen, Suchtexte, Ausnahmegründe, Passwörter,
Tokens, Secret-Werte und Originalmeldungen externer Systeme. Berichte protokollieren nur die Zahl
der Empfänger. Exporte protokollieren Filtercodes, aber nie den Suchbegriff.

## Authentifizierung und Benutzer

| Aktion | Auslöser und Detail |
|---|---|
| `auth.login` | erfolgreicher lokaler Login; `user_id` |
| `auth.login_failed` | falsches Passwort eines bekannten Kontos; Akteur `user:<id>`, Detail `user_id` |
| `auth.lockout` | Sperrschwelle erreicht; `user_id` |
| `auth.logout` | Logout; `user_id` |
| `auth.password_change` | eigener Passwortwechsel; `user_id` |
| `auth.sso_login` | erfolgreicher Entra-Login; `user_id` |
| `auth.sso_revoked` | SSO-Cache, Gruppe oder Revalidierung nicht mehr gültig; `user_id` |
| `user.create` | Initialkonto, CLI oder Admin-Oberfläche; `user_id`, beim Initialkonto zusätzlich `initial=ja` |
| `user.reset_password` | lokales Passwort zurückgesetzt; `user_id` |
| `user.activate` / `user.deactivate` | Aktivstatus geändert; `user_id` |
| `user.admin_on` / `user.admin_off` | lokale Admin-Rolle geändert; `user_id` |

Unbekannte Benutzernamen schreiben keinen Audit-Eintrag, damit keine vom Angreifer kontrollierten
Werte persistiert werden. Entra-Adminrollen werden aus Gruppen synchronisiert und erzeugen keinen
manuellen Rollen-Eintrag.

## Fachliche Aktionen

| Aktion | Detail |
|---|---|
| `sync.manual` | manueller Mandantenzyklus; `tenant_id` |
| `identity.account_type` | manuelle Kontoart oder `automatisch`; `identity_id`, technischer Art-Code |
| `identity.keep` / `identity.reopen` / `identity.delete` | Prüflistenentscheidung; `identity_id`, Zahl betroffener Ausnahmen |
| `exception.create` | `exception_id`, `identity_id`, Gültigkeitsdatum |
| `exception.delete` | `exception_id`, `identity_id` |
| `export.csv` / `export.xlsx` | Format, Zeilenzahl und gesetzte Filtercodes ohne Suchtext |
| `report.sent` | Zahl der Empfänger |
| `report.failed` | fester Mail-Fehlercode und optional bereinigter Graph-Code |

`identity.delete` betrifft nur die lokale Datenbank. Es ist nicht rückgängig zu machen, verändert
aber niemals Entra oder AD.

## Sicherungen

| Aktion | Detail |
|---|---|
| `backup.ok` | Dateiname, Größe und Zahl entfernter alter lokaler Sicherungen |
| `backup.failed` | fester lokaler Backup-Fehlercode |
| `external_backup.ok` | gehashter Zielmarker |
| `external_backup.init_ok` | Zielmarker nach Initialisierung |
| `external_backup.check_ok` | Zielmarker nach vollständiger Prüfung |
| `external_backup.restore_ok` | Zielmarker nach geprüftem Restore in neues Verzeichnis |
| `external_backup.prune_ok` | Zielmarker nach ausdrücklich bestätigter Aufbewahrung |
| `external_backup.failed` | Zielmarker, Operation und fester Fehlercode |

Der Zielmarker ist ein gekürzter SHA-256-Hash der Repository-Angabe. Ziel-URL, Dateipfade,
Zugangsdaten und Restic-Ausgaben werden nicht protokolliert.

## Auswertung

Für eine manuelle Untersuchung die Datenbank nur lesend und geschützt öffnen. Vor Weitergabe oder
Export gelten dieselben Datenschutzanforderungen wie für die Hauptdatenbank. Zeitfolge, Akteur,
Mandant, Aktion und technische IDs korrelieren; fachliche Namen werden bewusst über die aktuelle
Anwendungssicht aufgelöst und gehören nicht in ein dauerhaftes Audit-Extrakt.
