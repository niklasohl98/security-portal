# Plan: Inaktive-Konten-Wächter (Entra + AD)

Stand: 2. Nov. 2026 · Autor: Niklas · Dies ist das Konzept des Projekts (Phase A). Abweichungen
werden als ADR festgehalten, nicht stillschweigend im Code vollzogen.

## Ziel und Umfang

Das Tool liest täglich alle Benutzer aus Entra ID und dem On-Prem-AD beider Kunden (CG, CVH),
führt sie pro Person zusammen und markiert jedes Konto ohne Anmeldung seit mehr als 30 Tagen.
Ergebnis ist eine filterbare Liste im Browser, exportierbar als CSV und Excel. Das Tool liest nur,
es ändert oder löscht keine Konten.

**MVP (Version 1):**

- Mandanten CG und CVH in einer Instanz, je mit eigener Entra-App und eigener AD-Verbindung
- Täglicher Abruf per Zeitplan plus Knopf „Jetzt abrufen“
- Letzte Anmeldung aus Entra (interaktiv, nicht interaktiv, erfolgreich) und AD (lastLogon über
  alle DCs, lastLogonTimestamp)
- Flag „inaktiv > 30 Tage“, Schwellwert pro Mandant einstellbar
- Kontoart erkennen: Mitglied, Gast, Cloud-only, synchronisiert, Dienst-/Testkonto
- Lizenzen pro Konto anzeigen, damit Einsparungen sichtbar werden
- Ausnahmeliste (Break-Glass, Dienstkonten, freigegebene Postfächer) mit Begründung
- Export der aktuellen Filteransicht als CSV und XLSX

**Nicht im MVP (Nicht-Ziele):** Konten deaktivieren oder löschen, HR-Abgleich,
Mail-Benachrichtigungen, Verlaufsdiagramme. Diese stehen als spätere Ausbaustufen in Phase 5.

**Profil:** Streng (Produktivbetrieb, vollständige Benutzerlisten zweier Kunden, Dauerdienst).

## Architektur und Tech-Stack

Ein einziger Container mit Python-Backend liest beide Mandanten nur lesend, speichert in SQLite
und stellt die Liste hinter einem Reverse-Proxy per HTTPS bereit. Die Connectors laufen pro
Mandant nacheinander; erst danach führt der Sync Entra- und AD-Konten zusammen und setzt die Flags.

| Baustein | Wahl | Warum |
|---|---|---|
| Sprache | Python 3.12 | gute Bibliotheken für Graph, LDAP und Excel |
| Web | FastAPI, Jinja2, HTMX | kein JavaScript-Build, wenig Code |
| Datenbank | SQLite mit SQLAlchemy + Alembic | eine Datei, später auf Postgres umstellbar |
| Zeitplan | APScheduler im App-Prozess | kein zweiter Dienst nötig |
| Entra | msal + httpx | Zertifikats-Login, volle Kontrolle über Paging und Retry |
| AD | ldap3 über LDAPS | läuft im Linux-Container, kein Windows-Agent |
| Export | csv (Standardbibliothek), openpyxl | CSV mit BOM, formatierte Excel-Tabellen |
| Betrieb | Docker Compose, Caddy | HTTPS automatisch, ein Befehl zum Starten |

## Entra-Anbindung über Graph

Pro Mandant gibt es eine eigene App-Registrierung mit reinen Anwendungsberechtigungen
(Client-Credentials-Flow, kein Benutzerkontext). Authentifizierung per Zertifikat statt
Client-Secret, weil Zertifikate nicht im Klartext in Konfigurationen landen und länger laufen.

**Einrichtung je Mandant (CG, CVH):**

1. Entra Admin Center → App-Registrierungen → „Inaktive-Konten-Wächter“, nur dieser Mandant
2. API-Berechtigungen (Typ Anwendung): `User.Read.All`, `AuditLog.Read.All`,
   `Organization.Read.All`. *(Phase 2: `Group.Read.All` ist nicht nötig, gruppenbasierte
   Lizenzen kommen über `licenseAssignmentStates`.)*
3. Administratorzustimmung erteilen
4. Zertifikat erzeugen (selbstsigniert, 2 Jahre), öffentlichen Teil hochladen, privaten Schlüssel
   als Docker-Secret ablegen
5. Tenant-ID, Client-ID und Zertifikats-Fingerabdruck in die Mandantenkonfiguration eintragen

**Abfragen:**

| Zweck | Endpunkt |
|---|---|
| Benutzer mit Anmeldung und Lizenzen | `GET /v1.0/users?$select=id,displayName,userPrincipalName,mail,userType,accountEnabled,createdDateTime,onPremisesSyncEnabled,onPremisesImmutableId,onPremisesSamAccountName,signInActivity,assignedLicenses,licenseAssignmentStates&$top=999` |
| Lizenzbestand und Namen | `GET /v1.0/subscribedSkus` |
| Mandantenname, Domänen | `GET /v1.0/organization` |

**Regeln für den Abruf:**

- `@odata.nextLink` folgen, bis keiner mehr kommt; bei 651 Konten sind das 1 bis 2 Seiten.
  *(Phase 2: Mit `signInActivity` ist `$top` auf 500 begrenzt, daher 2 Seiten; Link nur auf
  `graph.microsoft.com`.)*
- HTTP 429 und 503 mit `Retry-After` und exponentiellem Backoff behandeln
- `signInActivity` braucht Entra ID P1 im Mandanten; fehlt sie, läuft der Abruf ohne Anmeldedaten
  weiter und die Oberfläche zeigt eine Warnung
- Letzte Entra-Anmeldung = jüngster Wert aus `lastSuccessfulSignInDateTime` und
  `lastNonInteractiveSignInDateTime`; `lastSignInDateTime` zählt auch fehlgeschlagene Versuche
  und wird nur angezeigt
- SKU-Namen über eine gepflegte Zuordnungstabelle (wie in der Excel-Auswertung), kostenlose SKUs
  ausblenden
- Bibliothek: msal für das Token, httpx für die Aufrufe; das Graph-SDK ist für diesen Umfang
  nicht nötig

## AD-Anbindung

Das Tool spricht das AD direkt per LDAPS (Port 636) aus dem Linux-Container an, mit der
Python-Bibliothek ldap3. Kein Windows-Agent, kein PowerShell-Remoting: weniger bewegliche Teile,
und der Container bleibt ein einziges Image. Voraussetzung ist, dass der Docker-Host die DCs
beider Kunden auf Port 636 erreicht.

Dienstkonto je Domäne: ein eigenes Konto (z. B. `svc-kontenwaechter`), Mitglied nur in
„Domain Users“, Passwort lang und zufällig, als Docker-Secret. Lesen der benötigten Attribute
reicht mit Standardrechten; Anmeldung interaktiv per GPO verbieten.

**Ablauf pro Lauf:**

1. DCs per DNS-SRV `_ldap._tcp.dc._msdcs.<domäne>` finden, ersatzweise feste Liste aus der Konfiguration
2. Gesundheitscheck je DC (siehe unten); defekte DCs für Stammdaten auslassen
3. Vom ersten gesunden DC alle Benutzer mit Paged Search (Seitengröße 1000) lesen: objectGUID,
   sAMAccountName, userPrincipalName, mail, userAccountControl, whenCreated, pwdLastSet,
   lastLogonTimestamp, accountExpires, description, distinguishedName, memberOf
4. Von jedem erreichbaren DC nur objectGUID + lastLogon lesen und pro Konto den jüngsten Wert nehmen
5. Letzte AD-Anmeldung = Maximum aus lastLogon (alle DCs) und lastLogonTimestamp
6. DCs, die nicht antworten, im Laufprotokoll und in der Oberfläche nennen, weil dann lastLogon zu alt sein kann

**Lehren aus LA-CG-GC:** Ein DC kann antworten und trotzdem leere Attribute liefern. Der
Gesundheitscheck prüft deshalb nicht nur die Erreichbarkeit, sondern liest 3 Referenzobjekte
(Dienstkonto, Administrator, krbtgt) und verlangt gefüllte Werte für userAccountControl und
whenCreated. Zusätzlich vergleicht er die Objektanzahl je DC mit dem Median: weicht ein DC um mehr
als 1 % ab, gilt er als verdächtig. Konten, deren Stammdaten auf dem Haupt-DC leer sind, werden
von einem zweiten DC nachgelesen und markiert.

**Zeitwerte:** lastLogon, lastLogonTimestamp, pwdLastSet sind Windows-FILETIME (100-ns-Schritte
seit 1601) in UTC; 0 und 9223372036854775807 bedeuten „nie“. lastLogonTimestamp repliziert mit bis
zu 14 Tagen Verzug, für die 30-Tage-Regel ist das ohne lastLogon aller DCs zu ungenau.

## Mandantenfähigkeit und Datenmodell

Jeder Kunde ist ein Mandant mit eigener Entra- und AD-Konfiguration; alle Tabellen tragen
`tenant_id`, und jede Abfrage filtert danach. Ein Mandant kann nur Entra, nur AD oder beides haben.
Neue Kunden kommen über die Oberfläche oder eine YAML-Datei dazu, ohne Codeänderung.

| Tabelle | Inhalt |
|---|---|
| tenant | Name, Kürzel, Entra-Tenant-ID, Client-ID, Zertifikatsverweis, AD-Domäne, DC-Liste, Bind-Konto-Verweis, Schwellwerte (inaktiv 30 Tage, Schonfrist neue Konten 14 Tage) |
| sync_run | Mandant, Quelle (entra/ad), Start, Ende, Status, Anzahl Konten, Fehler, geprüfte und defekte DCs |
| entra_account | Rohdaten je Konto und Lauf: Objekt-ID, UPN, Typ, aktiv, Anmeldewerte, Sync-Felder, Lizenzen |
| ad_account | Rohdaten je Konto und Lauf: objectGUID, sAMAccountName, UPN, UAC, lastLogon (max), lastLogonTimestamp, OU |
| identity | Zusammengeführte Person je Mandant: Verweise auf Entra- und AD-Konto, letzte Anmeldung gesamt, Kontoart, Flag, Empfehlung |
| exception | Ausnahme je Identität: Grund, angelegt von, gültig bis |
| sku_name | SKU-Teilenummer → Anzeigename, kostenlos ja/nein |
| audit_log | Wer hat wann was geändert oder exportiert |

**Konten zusammenführen (Entra ↔ AD), in dieser Reihenfolge:**

1. `onPremisesImmutableId` (Base64) = objectGUID des AD-Kontos, sicherste Zuordnung bei Entra Connect
2. `onPremisesSamAccountName` + Domäne = sAMAccountName
3. UPN gleich (Groß-/Kleinschreibung egal)
4. Kein Treffer: Konto steht allein als „nur Entra“ oder „nur AD“

Synchronisierte Entra-Konten ohne AD-Gegenstück werden als „verwaist“ markiert. Das ist genau der
Fall des alten Sync-Kontos `Sync_LA-CG-APP-01`, das seit 01/2024 nichts mehr synchronisiert.

Rohdaten bleiben 90 Tage, damit sich Veränderungen zwischen Läufen nachvollziehen lassen; danach
löscht ein nächtlicher Job sie.

## Flag-Logik

Ein Konto wird geflaggt, wenn seine jüngste Anmeldung aus allen Quellen (Entra erfolgreich, Entra
nicht interaktiv, AD lastLogon, AD lastLogonTimestamp) mehr als 30 Tage zurückliegt oder es nie
eine gab. Deaktivierte Konten zählen mit, bekommen aber einen eigenen Status, weil sie oft noch
Lizenzen tragen.

**Status** (eine Spalte, festgelegte Reihenfolge der Prüfung):

| Status | Bedingung |
|---|---|
| Ausnahme | Eintrag in der Ausnahmeliste und noch gültig |
| Neu | angelegt vor weniger als 14 Tagen und noch nie angemeldet |
| Deaktiviert | in Entra oder AD deaktiviert |
| Nie angemeldet | keine Anmeldung in keiner Quelle, älter als 14 Tage |
| Inaktiv > 90 Tage | letzte Anmeldung älter als 90 Tage |
| Inaktiv > 30 Tage | letzte Anmeldung älter als 30 Tage |
| Aktiv | letzte Anmeldung innerhalb von 30 Tagen |

**Kontoart** (automatisch, manuell überschreibbar): Mitglied synchronisiert, Mitglied Cloud-only,
Gast (userType = Guest oder `#EXT#`), Admin (Name enthält adm/admin oder Rolle mit Privilegien),
Dienst/Sync (`Sync_*`, `svc*`, `dienste.*`), Test (test, demo), Postfach/Ressource (kein Login,
z. B. besucher, wc). Die Namensmuster sind pro Mandant konfigurierbar.

**Empfehlung** (Textspalte, wie in der Excel-Auswertung):

- Lizenziert und inaktiv > 90 Tage: „Lizenz entziehen, dann deaktivieren“
- Gast nie angemeldet, Einladung älter als 90 Tage: „Gast löschen“
- Testkonto inaktiv: „Löschen“
- Postfach/Ressource: „Anmeldung blockieren, ggf. in freigegebenes Postfach umwandeln“
  (Postfächer nicht einfach entlizenzieren, sonst ist das Postfach nach 30 Tagen weg)
- Synchronisiert, aber im AD nicht gefunden: „Verwaist prüfen“
- In AD aktiv, in Entra inaktiv (oder umgekehrt): Hinweis „nur lokal genutzt“ bzw. „nur Cloud
  genutzt“, kein Löschvorschlag

Ausnahmen brauchen Grund und Ablaufdatum (Standard 12 Monate), damit Break-Glass- und
Dienstkonten regelmäßig neu bestätigt werden.

## Web-Frontend und Export

Die Oberfläche ist serverseitig gerendert (FastAPI + Jinja2 + HTMX), ohne eigenen
JavaScript-Build. Für die Tabelle reicht eine eingebundene Bibliothek wie Tabulator oder Grid.js.

| Seite | Inhalt |
|---|---|
| Übersicht | Je Mandant: Konten gesamt, geflaggt, davon lizenziert, freiwerdende Lizenzen nach SKU, Stand des letzten Laufs, Warnungen (defekter DC, fehlende P1-Lizenz, Zertifikat läuft ab) |
| Konten | Tabelle aller Identitäten mit Filtern: Mandant, Status, Kontoart, lizenziert ja/nein, Quelle, Freitext; Sortierung nach Tagen seit Anmeldung |
| Kontodetail | Alle Anmeldewerte nebeneinander (Entra je Art, AD je DC), Lizenzen, OU, Verlauf der letzten Läufe, Ausnahme anlegen |
| Ausnahmen | Liste mit Grund, Ersteller, Ablauf; bald ablaufende hervorgehoben |
| Läufe | Protokoll der Abrufe je Mandant und Quelle, Knopf „Jetzt abrufen“ |
| Prüfliste | Verschwundene Identitäten: behalten, löschen oder wieder prüfen (ADR 0006, umgesetzt) |
| Mandanten | Konfiguration anlegen und bearbeiten, Verbindung testen (Graph-Token, LDAPS-Bind, DC-Check) |

**Export:**

- Exportiert wird immer die aktuelle Filteransicht, wahlweise CSV oder XLSX
- CSV: Trennzeichen `;`, UTF-8 mit BOM, Datumsformat `TT.MM.JJJJ HH:MM`, damit Excel es direkt
  richtig öffnet
- XLSX (openpyxl): Blatt „Konten“ als formatierte Excel-Tabelle mit Filtern, fixierter Kopfzeile,
  farbigem Status; Blatt „Lizenzen“ mit freiwerdenden Lizenzen je SKU; Blatt „Info“ mit Mandant,
  Stichtag, Schwellwerten und Filtern des Exports
- Dateiname: `Inaktive_Konten_<Mandant>_<JJJJ-MM-TT>.xlsx`
- Jeder Export landet im Audit-Log
- Zusätzlich `GET /api/accounts?tenant=cg&status=inaktiv&format=csv` für Skripte, mit API-Token

## Sicherheit und Betrieb

Das Tool hält eine vollständige Benutzerliste zweier Kunden mit Anmeldeverhalten, es ist also
selbst ein lohnendes Ziel. Darum: nur Leserechte an den Quellen, keine Geheimnisse im Image oder
Repo, Zugang zur Oberfläche nur mit Anmeldung.

**Zugangsdaten:**

- Zertifikate (privater Schlüssel) und AD-Passwörter als Docker-Secrets unter `/run/secrets/`,
  nie in `.env`, Datenbank oder Git
- Die Mandantentabelle speichert nur den Namen des Secrets
- Zertifikatsablauf wird täglich geprüft, Warnung 30 Tage vorher

**Zugang zur Oberfläche:**

*Stand 6. Nov. 2026:* als erster Schritt gibt es eine lokale Anmeldung mit Benutzerdatenbank, Initialkonto
und erzwungenem Passwortwechsel (ADR 0007). Entra-OIDC und HTTPS-Proxy folgen.

- Anmeldung per Entra ID (OIDC) über den eigenen Softchem-Mandanten, Zugriff nur für eine
  Sicherheitsgruppe; Alternative für den Start: lokales Konto mit Argon2-Hash und TOTP
- Nur über HTTPS hinter einem Reverse-Proxy (Caddy oder Traefik im selben Compose), Port nur im
  internen Netz
- CSRF-Schutz für Formulare, sichere Cookie-Flags, Sitzungsablauf nach 8 Stunden

**Container:**

- Ein Image `python:3.12-slim`, läuft als Nicht-Root-Benutzer, Dateisystem schreibgeschützt bis
  auf `/data`
- `docker-compose.yml` mit Diensten app, proxy; Scheduler (APScheduler) läuft im App-Prozess, ein
  Lauf je Mandant und Quelle nacheinander mit Sperre gegen Doppelläufe
- LDAPS mit Zertifikatsprüfung: CA-Zertifikate beider Domänen werden ins Image-Truststore
  gemountet, kein `verify=False`
- Healthcheck-Endpunkt `/health`

**Betrieb:**

- SQLite-Datei unter `/data`, nächtliche Sicherung per `sqlite3 .backup` in ein gemountetes
  Backup-Verzeichnis, 14 Tage aufbewahren
- Logs als JSON auf stdout, ohne Passwörter oder Tokens
- Updates: neues Image bauen, `docker compose up -d`; Datenbankmigrationen mit Alembic beim Start
- Laufzeit pro Abruf: wenige Sekunden für Graph, für AD etwa 1 Minute pro 8 DCs

## Projektstruktur und Umsetzungsphasen

Claude Code baut das Projekt in 5 Phasen; jede endet mit grünen Tests und einer Abnahme durch
Niklas, bevor die nächste startet. Graph und AD werden in Tests gemockt (respx,
ldap3-Mock-Strategie), damit alles ohne echte Mandanten läuft.

```
./
  CLAUDE.md
  docker-compose.yml
  Dockerfile
  pyproject.toml
  config/tenants.example.yaml
  app/
    main.py            # FastAPI, Routen, Start des Schedulers
    config.py          # Einstellungen, Secrets laden
    db/models.py       # SQLAlchemy-Modelle
    db/migrations/     # Alembic
    connectors/graph.py
    connectors/ad.py   # LDAPS, DC-Check, lastLogon über alle DCs
    core/merge.py      # Entra ↔ AD zusammenführen
    core/rules.py      # Status, Kontoart, Empfehlung
    core/sync.py       # Lauf je Mandant und Quelle
    export/csv.py
    export/xlsx.py
    web/templates/
    web/static/
    auth/
  tests/
    fixtures/          # anonymisierte Graph- und LDAP-Antworten
```

| Phase | Inhalt | Abnahme |
|---|---|---|
| 1 Grundgerüst | Repo, Docker, FastAPI, Datenbank, Mandantenkonfiguration aus YAML, /health | `docker compose up` startet, /health gibt 200, Mandanten CG und CVH sind angelegt |
| 2 Entra | Graph-Connector mit Zertifikat, Paging, Retry, Lizenzen, Speichern als entra_account | Lauf gegen CG liefert 651 Konten und 20 SKUs wie im Graph Explorer; Test mit 2 Seiten Paging grün |
| 3 AD | LDAPS-Connector, DC-Erkennung, Gesundheitscheck, lastLogon über alle DCs | Lauf gegen cg.softchem.netz findet alle 8 DCs, meldet LA-CG-GC als verdächtig, Werte stimmen mit dem PowerShell-Skript für die 45 Testkonten überein |
| 4 Logik und Oberfläche | Zusammenführung, Status, Kontoart, Empfehlung, Seiten, Filter, Ausnahmen, Export CSV/XLSX, Scheduler | Quick-Win-Konten aus der Excel-Auswertung erscheinen geflaggt; CSV öffnet in Excel mit Umlauten korrekt |
| 5 Härtung und Ausbau | Login per Entra OIDC (Softchem-Mandant), Audit-Log, Backups, Reverse-Proxy; danach Mail-Bericht (gewünscht); Verlauf optional; HR-Abgleich zurückgestellt | Zugriff ohne Anmeldung unmöglich, Backup wiederherstellbar, keine Secrets in Logs |

**Regeln während der Umsetzung:** Typ-Hinweise und ruff sauber, pytest vor jedem Commit, keine
echten Mandantendaten in Fixtures oder Commits, Schreibzugriffe auf Entra oder AD sind ausgeschlossen.

## Entscheidungen und offene Punkte

Stand 2. Nov. 2026, nach Rückmeldung von Niklas.

**Entschieden:**

- **Entra ID P1:** CVH hat E3-Lizenzen (enthalten P1), `signInActivity` ist dort verfügbar. Für CG
  wird das mit dem ersten Lauf in Phase 2 sichtbar; fehlt P1, greift die Warnung aus dem Abruf-Konzept.
- **Netz:** Der Docker-Host erreicht alle DCs beider Kunden auf Port 636. Der Windows-Agent als
  Rückfall entfällt damit vorerst (ADR 0002 bleibt gültig).
- **Zielgruppe:** Nur die Softchem-IT nutzt die Oberfläche. Kein Rollenmodell mit Mandantensicht
  für Kunden; die Trennung per `tenant_id` bleibt trotzdem Pflicht in jeder Abfrage.
- **Schwellwert:** 30 Tage für alle Kontoarten und Mandanten (Standardwert). Er bleibt je Mandant
  konfigurierbar, aber nicht je Kontoart.
- **Betrieb:** Der Container läuft auf der Synology (siehe ADR 0004).
- **Backups:** Erst lokal auf der Synology; Ziel wird später festgelegt (siehe KI-6).
- **Anmeldung:** Entra ID (OIDC) im Softchem-Mandanten, Zugriff über eine Sicherheitsgruppe.
- **Benachrichtigung:** per Mail, Phase 5. SMTP-Weg noch zu klären (siehe unten).
- **HR-Abgleich:** zurückgestellt (Backlog).
- **LA-CG-GC:** wird später repariert; bis dahin meldet der DC-Check ihn bei jedem Lauf als verdächtig.

**Offen:**

- [x] **LDAPS-Zertifikate** auf allen DCs prüfen (Niklas), sonst vor Phase 3 ausrollen. Ohne TLS
      auf 389 ist keine Option. Zusätzlich brauchen wir die CA-Zertifikate beider Domänen.
      *Stand 3. Nov. 2026:* vom PC aus alle 10 DCs erreichbar mit gültigem Namen, Kette und Ablauf;
      im Container je Domäne stichprobenartig geprüft. CA-Dateien beider Domänen liegen vor (beide
      Stamm-CAs). **CG: RSA-1024-Schlüssel**, befristete Ausnahme siehe ADR 0005 und KI-13.
- [ ] **Synology:** DSM-Version und Container Manager (Compose-Unterstützung für `secrets`,
      `read_only`, `cap_drop`), CPU-Architektur (x86 oder ARM), Ablageort für Volumes und Secrets.
      Wird in Phase 5 am echten Gerät getestet.
- [ ] **Reverse-Proxy:** Caddy im Compose oder der Reverse-Proxy von DSM (Phase 5, ADR 0004).
- [ ] **Mail-Versand:** SMTP-Relay oder Graph `sendMail` aus dem Softchem-Mandanten? Empfänger
      und Rhythmus (z. B. wöchentlich je Mandant)? (Phase 5)
- [ ] **Backup-Ziel** außerhalb der Synology festlegen (Phase 5, KI-6).
- [ ] **Softchem-App-Registrierung für die Anmeldung** (delegiert, getrennt von den lesenden
      Apps der Kunden) und die Sicherheitsgruppe festlegen (Phase 5).
