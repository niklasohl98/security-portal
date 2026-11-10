# Nutzerhandbuch

Der Kontenwächter ist eine serverseitig gerenderte Oberfläche ohne JavaScript. Er zeigt den
Softchem-IT-Administratoren Konten und Risiken mehrerer Kundenmandanten. Änderungen in der
Oberfläche betreffen ausschließlich die lokale Datenbank; das Tool schreibt nie nach Kunden-Entra
oder Active Directory (AD).

## Rollen und Anmeldung

| Rolle | Rechte |
|---|---|
| lokaler Benutzer | Konten, Ausnahmen, Läufe und Prüfliste sehen und bearbeiten; Export und lokale Sicherung auslösen |
| lokaler Admin | zusätzlich Tool-Benutzer verwalten, Berichte bedienen und externe Sicherung auslösen |
| Entra-Benutzer | gleiche Fachrechte; Zugang und Admin-Rolle stammen aus den konfigurierten Gruppen |

Lokale Benutzer melden sich unter `GET /login` mit Benutzername und Passwort an. Das Initialkonto
`Admin` verlangt vor jeder anderen Seite einen Passwortwechsel. Nach zu vielen Fehlversuchen wird
das Konto vorübergehend gesperrt. Optional startet `GET /auth/entra/start` den Entra-Code-Flow;
`POST /auth/entra/callback` verarbeitet ausschließlich den browsergebundenen Callback. Ein
Entra-Konto erhält kein lokales Passwort.

`POST /logout` beendet die Tool-Sitzung. Bei Entra bleibt die übergeordnete Microsoft-
Browsersitzung bestehen. `GET /password` und `POST /password` ändern nur lokale Passwörter; für
Entra-Benutzer antworten beide Funktionen mit 403.

`POST /login` prüft die lokale Anmeldung. `GET /health` ist der nicht authentifizierte,
personenfreie Gesundheitscheck für Container und Betrieb.

## Übersicht

`GET /` zeigt je aktivem Mandanten:

- Zahl der vorhandenen, markierten und lizenzierten markierten Konten;
- Statusverteilung und potenziell freiwerdende, nicht kostenlose Lizenzen;
- jüngsten Entra-/AD-Lauf mit Warnungen;
- offene verschwundene Identitäten und ablaufende/abgelaufene Ausnahmen;
- Zustand der lokalen und optionalen externen Sicherung;
- Warnungen für inventarisierte öffentliche Zertifikate.

Kennzahlen verlinken direkt in die passende gefilterte Liste. „Freiwerdend“ ist eine Empfehlung,
kein Nachweis tatsächlicher Produktnutzung und keine automatische Lizenzänderung.

## Kontenliste und Detail

`GET /accounts` startet mit markierten Konten. Filter lassen sich kombinieren:

| Parameter | Werte |
|---|---|
| `tenant` | Mandantenkürzel; leer bedeutet alle aktiven Mandanten |
| `scope` | `flagged` (Standard) oder `all` |
| `status` | `exception`, `new`, `disabled`, `unknown`, `never`, `inactive_long`, `inactive`, `active` |
| `type` | `member_synced`, `member_cloud`, `member_onprem`, `guest`, `admin`, `service`, `test`, `resource` |
| `licensed` | `yes` oder `no`; kostenlose SKUs zählen nicht als lizenziert |
| `source` | `entra`, `ad` oder `both` |
| `q` | Suche in Anzeigename, UPN, Anmeldename und Mailadresse; höchstens 100 Zeichen |
| `page` | Seite ab 1; 200 Einträge pro Seite |

Unbekannte Filter werden ignoriert und nicht zurückgespiegelt. Sortiert wird nach Tagen seit der
Anmeldung, wobei nie angemeldete Konten zuerst stehen.

`GET /accounts/{slug}/{identity_id}` zeigt Entra- und AD-Werte getrennt, einschließlich aller
bewerteten Anmeldezeitpunkte, AD-`lastLogon` je Domain Controller, Lizenzen und Ausnahmen.

`POST /accounts/{slug}/{identity_id}/exception` legt eine befristete Ausnahme an. Grund ist Pflicht
und höchstens 500 Zeichen lang; das Datum muss in der Zukunft liegen und gilt bis zum Ende des
gewählten Tages in `KW_TIMEZONE`. Je Konto darf nur eine gültige Ausnahme existieren.

`POST /accounts/{slug}/{identity_id}/type` überschreibt die automatisch erkannte Kontoart. Ein
leerer Wert schaltet zurück auf automatische Erkennung. Ausnahme und Kontoart lösen sofort eine
Neubewertung des Mandanten aus.

## Ausnahmen und Prüfliste

`GET /exceptions` listet gültige, bald ablaufende und abgelaufene Ausnahmen nach Fälligkeit.
`POST /exceptions/{slug}/{exception_id}/delete` entfernt eine Ausnahme und bewertet das Konto neu.

Eine Identität, die in den jüngsten brauchbaren Quellen nicht mehr vorkommt, wird nicht automatisch
gelöscht. `GET /review` zeigt offene und bereits als „behalten“ entschiedene Einträge.

- `GET /review/{slug}/{identity_id}/delete` zeigt vor dem lokalen Löschen eine Bestätigung.
- `POST /review/{slug}/{identity_id}` akzeptiert `keep`, `delete` oder `reopen`.
- `delete` löscht nur die lokale Identität samt Ausnahmen; Quellen bleiben unverändert.

Vor einer großen Zahl verschwundener Konten immer zuerst die Laufwarnungen und DC-Gesundheit
prüfen. Unvollständige Quelldaten können die Ursache sein.

## Läufe, Sicherungen und Zertifikate

`GET /runs` zeigt die letzten 100 Abrufe, die letzten zehn lokalen Sicherungen, externen
Sicherungsstatus und Zertifikatsinventar.

- `POST /runs/{slug}/start` startet einen vollständigen Mandantenzyklus im Hintergrund. Läuft
  bereits einer, wird kein zweiter gestartet.
- `POST /backup/start` erstellt sofort eine lokale, geprüfte SQLite-Sicherung.
- `POST /backup/external/start` ist nur für Admins sichtbar und startet ausschließlich ein bereits
  konfiguriertes externes Backup. Es initialisiert oder bereinigt kein Repository.

Fortschritt wird durch Neuladen der Seite sichtbar. Fehler und Warnungen erscheinen als feste Codes
beziehungsweise übersetzte Texte ohne externe Antwortinhalte.

## Export

`GET /export` mit `format=csv|xlsx` exportiert dieselbe Filteransicht wie `/accounts`, aber alle
Treffer statt nur einer Seite. CSV verwendet Semikolon, UTF-8 mit BOM und CRLF. XLSX enthält die Blätter
„Konten“, „Lizenzen“ und „Info“. Text wird gegen Formel-Injection abgesichert; Zeitangaben erscheinen
in `KW_TIMEZONE`. Jeder Export wird ohne Suchtext im Audit-Log vermerkt.

## Benutzerverwaltung (Admins)

`GET /users` zeigt lokale und Entra-Benutzer. Admins können über `POST /users` einen lokalen Benutzer
anlegen und über folgende Routen verwalten:

- `POST /users/{user_id}/reset` erzeugt ein neues Einmalpasswort, aktiviert/entsperrt das Konto und
  beendet seine Sitzungen;
- `POST /users/{user_id}/active` aktiviert oder deaktiviert;
- `POST /users/{user_id}/admin` vergibt oder entzieht die lokale Admin-Rolle.

Einmalpasswörter erscheinen genau einmal und werden weder gespeichert noch geloggt. Das eigene
Konto lässt sich hier nicht verändern, der letzte aktive lokale Admin bleibt erhalten und
Entra-Passwort/Admin-Rolle werden ausschließlich in Entra verwaltet.

## Berichte (Admins)

`GET /reports` zeigt Mailkonfiguration, Zeitplan und Empfängerzahl. `GET /reports/{slug}` rendert
genau den Text der nächsten Mail, aber sendet nichts. `POST /reports/check` prüft Zertifikat und
Tokenbezug; erst `POST /reports/{slug}/send` prüft zusätzlich die Exchange-Sendeberechtigung und
sendet an die konfigurierten Empfänger. Berichte enthalten nur Kennzahlen, keine einzelnen Konten.

## Schutz aller Formulare

Jede zustandsändernde Route benötigt eine gültige Sitzung, Same-Origin und das CSRF-Token der
Sitzung. Ausnahmen sind das Login ohne bestehende Sitzung und der durch State/Nonce/Browsercookie
gebundene Entra-Callback. HTML wird nicht gecacht; Jinja maskiert Ausgaben. Technische Details und
Header stehen in der [Architektur](architecture.md), alle protokollierten Änderungen im
[Audit-Katalog](audit-log.md).
