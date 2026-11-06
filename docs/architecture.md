# Architektur (Stand Phase 5: Anmeldung, Backups)

Gesamtbild und Begründungen: [plan.md](plan.md). Diese Datei beschreibt, was **tatsächlich
implementiert** ist, und wird mit jeder Änderung nachgezogen.

## Laufzeit
Ein Container (`python:3.12-slim`, Nicht-root UID 10001, Dateisystem schreibgeschützt außer `/data`)
startet `uvicorn --factory app.main:create_app`. Beim Start (Lifespan):

1. Logging auf JSON/stdout (`app/logging_config.py`)
2. Mandanten-YAML laden und validieren (`app/config.py`); Fehler brechen den Start ab
3. `alembic upgrade head` (`app/db/session.py`)
4. Mandanten aus der YAML in die Tabelle `tenant` abgleichen (`app/core/tenants.py`):
   anlegen/aktualisieren per Kürzel, fehlende deaktivieren
5. SKU-Namen in `sku_name` abgleichen (`app/core/sku_names.py`): zuerst der mitgelieferte Katalog
   (`app/data/sku_catalog.yaml`, siehe unten), danach die optionale eigene `sku_names.yaml`, die bei
   gleicher Teilenummer gewinnt; Einträge nur in der DB bleiben erhalten
6. Unterbrochene Läufe (`running`) als `failed` abschließen (`app/core/sync.py`)

`GET /health` prüft die Datenbank (`SELECT 1`): 200 `{"status":"ok"}` oder 503. Er gibt keine
Mandantendaten preis. OpenAPI und Doku-Seiten sind abgeschaltet.

## Konfiguration
Umgebungsvariablen `KW_*` (siehe README) und `config/tenants.yaml`. Secrets werden nur über
`read_secret(name, secrets_dir)` aus `/run/secrets/` gelesen; der Name ist auf `[A-Za-z0-9._-]`
beschränkt (kein Pfadausbruch). In der YAML und in der Tabelle steht nur der Secret-*Name*.

## Datenmodell
`app/db/models.py`. Jede Tabelle außer `tenant`, `sku_name`, `app_user` und `app_session` trägt `tenant_id`. Zeitwerte nutzen
`UTCDateTime` (lehnt naive Werte ab, liefert UTC-aware). Enums sind VARCHAR mit CHECK. Constraints
sind benannt (Naming Convention), damit SQLite-Batch-Migrationen sie ansprechen können. Ein Test
(`test_models_and_migrations_are_in_sync`) schlägt fehl, wenn Modell und Migration auseinanderlaufen.
`identity.key` ist über Läufe hinweg stabil (Ausnahmen hängen daran); Rohdaten-Verweise sind
`ON DELETE SET NULL`, damit der 90-Tage-Bereinigungsjob (Phase 4/5) Identitäten nicht beschädigt.

## Migrationen schreiben
```bash
PYTHONPATH=. .venv/bin/python -c "
import tempfile; from alembic import command; from alembic.config import Config
c = Config(); c.set_main_option('script_location','app/db/migrations')
c.set_main_option('sqlalchemy.url', f'sqlite:///{tempfile.mkdtemp()}/x.db')
command.upgrade(c, 'head'); command.revision(c, message='Beschreibung', autogenerate=True)"
```
Danach prüfen und eigene Typen (`UTCDateTime`) durch `sa.DateTime(timezone=True)` ersetzen.

## Entra-Abruf (Phase 2)
`app/connectors/graph.py` (nur `GET`, nie Schreibzugriff), `app/core/sync.py` (Lauf).

1. `build_graph_client`: privater Schlüssel aus dem Docker-Secret, Token per `msal`
   (Client-Credentials, Zertifikat mit SHA-1-Fingerabdruck aus der YAML).
2. `fetch_skus` (`/subscribedSkus`), dann `fetch_users` (`/users`, Paging über `@odata.nextLink`).
   Mit `signInActivity` ist `$top` auf 500 begrenzt (Graph-Doku), ohne auf 999.
3. Antwortet Graph auf `signInActivity` mit 403, wird einmal ohne wiederholt: Lauf-Status
   `partial`, Warnung `sign_in_activity_unavailable` (P1/P2 fehlt oder `AuditLog.Read.All`
   nicht erteilt). Ein zweiter 403 ist ein echter Berechtigungsfehler und lässt den Lauf scheitern.
4. Wiederholung bei 429/502/503/504 und Netzwerkfehlern: `Retry-After` (Sekunden, höchstens 300),
   sonst exponentiell (Start 1 s, Obergrenze 60 s), höchstens 5 Wiederholungen.
5. `@odata.nextLink` wird nur befolgt, wenn er auf `https://graph.microsoft.com` (Port 443)
   zeigt; sonst bricht der Abruf ab, damit das Token nie an einen fremden Host geht.
6. Speichern in einer Transaktion: `entra_account` und `tenant_sku` je Lauf, danach Lauf auf
   `success`/`partial` (mit `account_count`, `warnings`). Schlägt der Abruf fehl, steht der Lauf
   auf `failed` mit `error` = Fehlerart (+ Status/Code bei Graph), nie mit Meldungstext.
7. Eine Sperre je (Mandant, Quelle) verhindert Doppelläufe im Prozess (`SyncBusyError`).

`entra_account.licenses` enthält je SKU `sku_id`, `direct`, `group` und `state` (schlechtester
Zustand, sonst `Active`). Kostenlose SKUs werden gespeichert und erst in der Anzeige
ausgeblendet (`sku_name.is_free`, ab Phase 4). Die Konten-Zusammenführung, Status und Flags
kommen in Phase 4; bis dahin liegen nur Rohdaten vor.

## AD-Abruf (Phase 3)
`app/connectors/ad_client.py` (LDAPS, ldap3), `ad_parse.py` (Werte), `ad.py` (Ablauf),
`app/core/ad_sync.py` (Lauf, Speichern). Anleitung: [ad-setup.md](ad-setup.md).

1. **DCs finden** (`discover_dcs`): feste Liste `ad.dcs` aus der Konfiguration, sonst DNS-SRV
   `_ldap._tcp.dc._msdcs.<domäne>` (`dnspython`, Zeitlimit 5 s). Namen klein und sortiert.
2. **Verbindung** (`open_ldaps`): Port 636 mit TLS ab dem ersten Byte, `CERT_REQUIRED`, Kette gegen
   `ad.ca_file` (sonst Zertifikatsspeicher des Containers), Hostname gegen den DC-FQDN (ldap3
   prüft ihn selbst nach dem Handshake, da Pythons `ssl.match_hostname` ab 3.12 fehlt; ein Test
   belegt es). Die OpenSSL-Sicherheitsstufe wird immer ausdrücklich gesetzt: 2 (RSA ab 2048
   Bit); nur mit der befristeten Ausnahme `ad.accept_weak_dc_keys_until` und nur bis zu diesem
   Datum Stufe 1 (ADR 0005). Einfaches Bind mit `<name>@<domain>`.
3. **Schreibschutz doppelt:** `read_only=True` sperrt in ldap3 nur `modify`, `delete`, `modify_dn`;
   `add` und `extended` sperrt `_ReadOnlyConnection` zusätzlich (Tests). Nach außen bietet
   `LdapsDirectory` nur `root_naming_context`, `search` (Paged Search, Seitengröße 1000) und `close`.
4. **Gesundheitscheck je DC** (`check_reference_objects`): Dienstkonto, `krbtgt`, `Administrator`
   mit gefüllter `userAccountControl` und `whenCreated`; `Administrator` darf fehlen. Sonst `defective`.
   Diese Konten sind geschützt und fallen bei einem Replikationsdefekt oft nicht auf. Deshalb liefert
   dieselbe `lastLogon`-Suche je DC auch `userAccountControl` und `whenCreated`; fehlen sie bei einem
   DC für mehr als 1 % der Konten, die ein anderer DC liefert, ist er `defective`
   (`accounts_empty`, `_flag_empty_account_data`). Werte, die auf keinem DC lesbar sind, zählen nicht.
5. **Stammdaten** vom ersten gesunden DC (alphabetisch), Filter
   `(&(objectCategory=person)(objectClass=user)(!(sAMAccountName=krbtgt_*)))` (ohne die
   Schlüsselkonten schreibgeschützter DCs; `krbtgt` bleibt). Leere Stammdaten werden einmal von einem zweiten
   gesunden DC ergänzt; betroffene Konten behalten `master_data_incomplete = true`.
6. **`lastLogon` aller erreichbaren DCs** (auch defekter), je Konto das Maximum in `last_logon`,
   Einzelwerte in `last_logon_per_dc`. `last_logon_timestamp` bleibt getrennt; das Gesamtmaximum
   mit Entra bildet Phase 4.
7. **Objektanzahl** je gesundem DC gegen den Median: Abweichung über 1 % = `suspect`, ab 3 DCs.
   Werden die Stammdaten von einem DC gelesen, der danach als Ausreißer auffällt, bleibt der Lauf
   gespeichert und die Warnung weist darauf hin.
8. **Speichern** wie beim Entra-Abruf in einer Transaktion: `ad_account` je Lauf, `sync_run` mit
   `status` (`success`/`partial`/`failed`), `account_count`, `dcs_checked`, `dcs_defective`
   (Liste aus `host`/`status`/`reason`) und `warnings`. `partial` bei `ad_dc_unreachable`,
   `ad_dc_defective`, `ad_dc_suspect`, `ad_master_data_incomplete`; die Hinweise
   `ad_weak_key_exception_active|expired` ändern den Status nicht. Ohne gesunden DC: `failed`
   mit `AdError code=no_healthy_dc` (die DC-Einzelergebnisse werden trotzdem gespeichert).
   Fehlertexte enthalten nur feste Codes (`tls`, `connect`, `bind`, `ldap`, `no_dc_found`).
9. Eine Sperre je (Mandant, `Source.AD`); Entra- und AD-Lauf blockieren sich nicht.

Zeitwerte: `lastLogon`, `lastLogonTimestamp`, `pwdLastSet`, `accountExpires` sind FILETIME (UTC);
`0` und `0x7FFFFFFFFFFFFFFF` ergeben `None` („nie“). `whenCreated` ist GeneralizedTime.
Die Mandantentabelle trägt zusätzlich `ad_ca_file` und `ad_weak_keys_until` (Migration `0003`).

Befehle: `python -m app.cli check-ad <kürzel>` (Trockenlauf, speichert nichts) und `sync-ad <kürzel>`.

## Zusammenführen und Regeln (Phase 4, Etappe 1)
`app/core/merge.py` (Zuordnung), `rules.py` (Regeln), `identities.py` (Speichern). Befehl:
`python -m app.cli refresh-identities <kürzel>`. Alles arbeitet nur auf der Datenbank.

1. **Datenbasis:** je Quelle der jüngste Lauf mit `success` oder `partial`; `failed` zählt nicht.
   Gibt es keinen brauchbaren Lauf, bleibt `identity` unverändert. Ohne AD-Lauf bleibt jedes
   Entra-Konto allein (und „verwaist“ wird nicht behauptet).
2. **Zuordnung** (`merge_accounts`), jede Stufe nur für noch offene Konten: `onPremisesImmutableId`
   (Base64 der 16 `objectGUID`-Bytes in Windows-Reihenfolge, `uuid.UUID(bytes_le=…)`) = `object_guid`,
   dann `onPremisesSamAccountName` = `sAMAccountName`, dann UPN (ohne Groß-/Kleinschreibung). Ein
   Schlüssel mit mehr als einem Treffer ordnet nichts zu (nicht raten); die Zahl steht im Ergebnis
   (`ambiguous`). Die AD-Domäne wird nicht verglichen: ein Mandant hat genau eine.
3. **Letzte Anmeldung** = Maximum aus Entra erfolgreich, Entra nicht interaktiv, AD `lastLogon`
   (alle DCs) und AD `lastLogonTimestamp`. `lastSignInDateTime` fließt nicht ein.
4. **Status** in fester Reihenfolge: `exception` (gültige Ausnahme), `new` (jünger als
   `new_account_grace_days` und nie angemeldet), `disabled` (Entra `accountEnabled=false` oder
   AD-Bit `ACCOUNTDISABLE`), `unknown`, `never`, `inactive_long`, `inactive`, `active`. Grenzen sind
   „mehr als“ (genau 30 Tage ist noch aktiv); die Tage kommen aus der Mandantenkonfiguration.
   `unknown` (nicht im Plan): Lieferte Graph keine Anmeldewerte (Warnung
   `sign_in_activity_unavailable`), ist ein Entra-Konto ohne jüngste AD-Anmeldung nicht beurteilbar
   und wird nicht geflaggt. Sonst stünde nach einem Lauf ohne P1-Lizenz jedes Konto auf „nie angemeldet“.
5. **Flag** (`identity.flagged`): letzte Anmeldung älter als `inactive_days` oder nie; ausgenommen
   `exception`, `new`, `unknown`. Deaktivierte Konten zählen mit.
6. **Kontoart:** Gast (`userType=Guest` oder `#EXT#`), dann Namensmuster in der Reihenfolge admin,
   service, test, resource (Mandantenkonfiguration `account_patterns`, geprüft gegen UPN-Teil und
   sAMAccountName), sonst `member_synced`, `member_cloud` oder `member_onprem` (nur im AD; im Plan
   nicht genannt). Vorgabe nur für admin: `*adm*`. Eine manuelle Kontoart (`account_type_override`)
   bleibt erhalten und ersetzt die automatische für die Empfehlung.
7. **Empfehlung** (deutscher Text): Postfach/Ressource und geflaggt → „Anmeldung blockieren …“;
   Gast nie angemeldet und älter als `long_inactive_days` → „Gast löschen“; Testkonto geflaggt →
   „Löschen“; lizenziert und länger als `long_inactive_days` ohne Anmeldung → „Lizenz entziehen,
   dann deaktivieren“ (bei bereits deaktivierten nur „Lizenz entziehen“); synchronisiert ohne
   AD-Gegenstück (bei vorhandenen AD-Daten) → „Verwaist prüfen“; aktiv, aber nur in einer Quelle
   jüngst genutzt → „Nur lokal genutzt“ bzw. „Nur Cloud genutzt“. „Lizenziert“ heißt: mindestens eine
   Lizenz, deren SKU nicht in `sku_name` als kostenlos steht.
8. **Speichern:** eine Zeile je Konto in `identity`. `key` = Entra-Objekt-ID, sonst `objectGUID`.
   Bekommt ein bisher einzelnes Konto später einen Partner, wird die Zeile umgestellt und behält
   Ausnahmen und manuelle Kontoart; gehen zwei Zeilen in einer auf, wandern die Ausnahmen mit.
   Zeilen ohne Konto im aktuellen Stand werden **nicht** gelöscht, sondern als verschwunden
   markiert (siehe unten). Mandantentrennung: jede Abfrage filtert nach `tenant_id`.

Die Berechnung läuft automatisch nach jedem Zyklus (siehe „Zeitplan und Zyklus“) und nach jeder
Änderung einer Ausnahme; von Hand per `refresh-identities`.

## Verschwundene Identitäten und Prüfliste (Phase 4, Etappe 2a, ADR 0006)
`app/core/identities.py` markiert Zeilen ohne Konto: `missing_since` (Zeitpunkt der ersten
Feststellung, bleibt beim Wiederholen), Status `missing`, `flagged = false`, keine Empfehlung.
Taucht das Konto wieder auf, setzt der nächste Lauf `missing_since`, `review_decision` und
`reviewed_at` zurück. Gelöscht wird nie automatisch. `RefreshResult.missing` zählt die noch
offenen (ohne Entscheidung); die CLI nennt sie.

`app/core/review.py` setzt Entscheidungen um (`decide`): `keep` (nur offene), `delete` (offene
und behaltene; Zeile samt Ausnahmen), `reopen` (nur behaltene). Jede Abfrage filtert nach
`tenant_id`; eine ID eines anderen Mandanten ist `not_found`. Abgelehnte Entscheidungen
(`ReviewError`: `not_found`, `not_missing`, `bad_state`) ändern nichts und schreiben nichts ins
Audit-Log. Jede ausgeführte Entscheidung schreibt `audit_log` (`identity.keep|delete|reopen`,
Akteur, `identity_id=… ausnahmen=…`, keine Namen).

## Oberfläche (`app/web/`, Etappen 2a und 3)
Jinja2-Seiten ohne JavaScript und ohne HTMX (bisher nicht nötig), statisch unter `/static`.
Daten kommen aus `app/core/accounts.py` (Konten laden, filtern, sortieren) und `overview.py`
(Kennzahlen, Läufe); die Routen liegen in `review.py`, `pages.py`, `exceptions_page.py`, `runs.py`.

| Pfad | Zweck |
|---|---|
| `GET /` | Übersicht je Mandant: Konten, geflaggt, davon lizenziert, verschwunden (offen), Ausnahmen bald ablaufend/abgelaufen, freiwerdende Lizenzen nach SKU, letzter Lauf je Quelle, Warnungen |
| `GET /accounts` | Kontenliste; Parameter `tenant`, `scope` (`flagged` Standard, `all`), `status`, `type`, `licensed` (`yes`/`no`), `source` (`entra`/`ad`/`both`), `q`, `page`; sortiert nach Tagen seit Anmeldung (nie angemeldet zuerst), 200 je Seite |
| `GET /accounts/<kürzel>/<id>` | Detail: Entra-Werte und AD-Werte nebeneinander (auch `lastLogon` je DC), Lizenzen, Ausnahmen, manuelle Kontoart |
| `POST /accounts/<kürzel>/<id>/exception` | Ausnahme anlegen (`reason`, `valid_until` = `JJJJ-MM-TT`, gilt bis Tagesende Ortszeit) |
| `POST /accounts/<kürzel>/<id>/type` | manuelle Kontoart setzen (`account_type`; leer = automatisch) |
| `GET /exceptions`, `POST /exceptions/<kürzel>/<id>/delete` | Ausnahmen je Mandant (abgelaufene und bald ablaufende hervorgehoben), Entfernen |
| `GET /runs`, `POST /runs/<kürzel>/start` | Protokoll der letzten 100 Läufe; „Jetzt abrufen“ startet den Zyklus des Mandanten im Hintergrund (`BackgroundTasks`), gesperrt solange einer läuft |
| `GET /review`, `GET /review/<kürzel>/<id>/delete`, `POST /review/<kürzel>/<id>` | Prüfliste verschwundener Identitäten (ADR 0006), Löschen nur nach Bestätigungsseite |

| `GET /export?format=csv\|xlsx&…` | Export der aktuellen Filteransicht (dieselben Parameter wie `/accounts`, ohne `page`), siehe „Export“ |

Alle Änderungen betreffen nur diese Datenbank und stehen im Audit-Log (nur IDs und Codes, nie Namen
oder Gründe): `identity.keep|delete|reopen`, `exception.create|delete`, `identity.account_type`,
`sync.manual`, `export.csv|xlsx`, `report.sent|failed`. Der Akteur ist der angemeldete Benutzer.

**Filter:** `web/filters.py` liest die Parameter; ungültige Werte werden ignoriert und nie
zurückgegeben. Die Meldungen der Seiten (`?done=`, `?err=`) stammen aus festen Listen. **Zeiten**
erscheinen in `KW_TIMEZONE` als `TT.MM.JJJJ HH:MM` (Zeitzone im Seitenfuß; `labels.format_dt`).
**Kontoart-Anzeige:** die manuelle Kontoart ersetzt die automatische und trägt das Kennzeichen
„manuell“. „Lizenziert“ heißt: mindestens eine Lizenz, deren SKU nicht als kostenlos gilt.

Schutzmaßnahmen (Anmeldung siehe oben): Antwort-Header (`Content-Security-Policy`
ohne Skripte, `X-Frame-Options: DENY`, `nosniff`, `Cache-Control: no-store` für HTML,
`Referrer-Policy: same-origin`). Nicht `no-referrer`: Browser senden dann bei Formularen
`Origin: null`, und die CSRF-Prüfung lehnte die eigene Seite ab (im Browser geprüft). Jede
ändernde Route (POST) verlangt Same-Origin und das CSRF-Token: `Sec-Fetch-Site` muss `same-origin`/`none` sein und
`Origin` zum `Host` passen; fehlen beide (Skripte), gilt die Anfrage als zulässig. Formulare
werden ohne `python-multipart` gelesen (nur `application/x-www-form-urlencoded`, höchstens 4 KiB,
höchstens 20 Felder). Jinja2 maskiert alle Ausgaben (Autoescape).

## Anmeldung (Phase 5, lokale Benutzerdatenbank, ADR 0007)
`app/auth/` (`passwords.py`, `service.py`, `bootstrap.py`, `context.py`, `middleware.py`), Seiten in
`app/web/auth_pages.py`, Tabellen `app_user` und `app_session` (Migration `0006`; nicht
mandantenbezogen, siehe Datenmodell).

- **Zugriffsschutz** (`add_auth`): Middleware lädt die Sitzung aus dem Cookie `kw_session`. Ohne
  gültige Sitzung sind nur `/login`, `/health` und `/static/*` erreichbar; sonst GET → 303 auf
  `/login?next=<Ziel>`, andere Methoden → 401. Sie sitzt innerhalb der Sicherheits-Header, damit auch
  Umleitungen diese tragen.
- **Initialkonto:** `ensure_initial_admin` beim Start, nur bei leerer Tabelle: `Admin` mit dem
  Startpasswort `Passwort123` (`app/auth/bootstrap.py`), `must_change_password = true`. Solange der
  Wechsel offen ist, sind nur `/password`, `/logout`, `/static` und `/health` erreichbar (GET → 303 auf
  `/password`, POST → 403). Das Startpasswort ist als neues Passwort verboten.
- **Passwörter:** Argon2id (`argon2-cffi`, Voreinstellungen = RFC-9106-Profil mit wenig Speicher),
  NFKC-normalisiert, Rehash beim Login bei geänderten Parametern. Regeln für neue Passwörter: 12 bis
  128 Zeichen, nicht der Benutzername darin, nicht das Startpasswort, nicht das aktuelle; keine
  Zeichenklassen-Pflicht. Anmeldeeingaben über 1024 Zeichen sind sofort ungültig.
- **Anmeldung** (`authenticate`): Benutzername ohne Beachtung der Groß-/Kleinschreibung. Falsches
  Passwort zählt hoch; ab `KW_LOGIN_MAX_ATTEMPTS` (5) Sperre für `KW_LOGIN_LOCKOUT_MINUTES` (15).
  Unbekannter oder deaktivierter Benutzer, falsches Passwort und Sperre ergeben denselben Text, und
  für Unbekannte wird gegen einen Scheinhash gerechnet. Fehlversuche unbekannter Benutzer schreiben
  nichts in die Datenbank (nur eine feste Logzeile).
- **Sitzungen:** Token (32 Byte zufällig) nur als SHA-256-Hash in `app_session`, Ablauf nach
  `KW_SESSION_HOURS` (8) absolut, neues Token bei jeder Anmeldung und nach dem Passwortwechsel;
  Abmelden, Passwortwechsel (alle anderen), Zurücksetzen und Deaktivieren beenden Sitzungen.
  Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` bei HTTPS-Anfragen (oder mit `KW_COOKIE_SECURE=1`
  erzwungen).
- **CSRF:** neben der Same-Origin-Prüfung ein Token je Sitzung (`app_session.csrf_token`) im Feld
  `csrf_token` jedes Formulars; `form_fields` vergleicht zeitkonstant (403 bei Fehlen, 401 ohne
  Sitzung). Das Login-Formular hat kein Token (noch keine Sitzung), nur die Same-Origin-Prüfung.
- **Seiten:** `GET|POST /login` (`next` nur als Pfad dieser Seite, sonst `/`), `POST /logout`,
  `GET|POST /password`. Die Navigation zeigt den Benutzer und „Abmelden“.
- **Audit-Log:** Akteur ist der Benutzername. `auth.login`, `auth.logout`, `auth.login_failed`
  (Akteur `user:<id>`, nur Benutzer-ID), `auth.lockout`, `auth.password_change`, `user.create`,
  `user.reset_password`; nie Passwörter oder fremde Eingaben.
- **Befehle:** `python -m app.cli user-list|user-add <name>|user-reset <name>` (Passwort ohne
  Anzeige, zweimal; der Wechsel beim ersten Login bleibt Pflicht). `user-reset` hebt Sperre und
  Deaktivierung auf und beendet Sitzungen. Zugang ohne Oberfläche, falls der Admin ausgesperrt ist.

### Benutzerverwaltung (Admins)
`app/auth/users.py` (Logik), `app/web/users_page.py` (Seiten, alle hinter `require_admin`, sonst 403),
Vorlagen `users.html` und `user_secret.html`. Spalte `app_user.is_admin` (Migration `0007`).

- **Einmalpasswort:** `generate_temp_password` zieht 16 Zeichen per `secrets` aus einem Alphabet ohne
  verwechselbare Zeichen und wiederholt, bis die Passwortregeln erfüllt sind. Es steht nur in der
  Antwort des Anlegens/Zurücksetzens (`Cache-Control: no-store` durch die Sicherheits-Header), nie in
  Datenbank, Log oder Audit-Log; `must_change_password` ist gesetzt.
- **Schutzregeln:** `self` (nicht das eigene Konto), `last_admin` (der letzte aktive Admin lässt sich
  weder deaktivieren noch herabstufen), `not_found`. Zurücksetzen hebt Sperre und Deaktivierung auf und
  beendet Sitzungen; Deaktivieren beendet Sitzungen.
- **Routen:** `GET|POST /users`, `POST /users/<id>/reset|active|admin`; alle ändernden mit Same-Origin-
  und CSRF-Prüfung. Meldungen kommen aus festen Listen (`?done=`, `?err=`).
- **Audit-Log:** `user.create`, `user.reset_password`, `user.activate`, `user.deactivate`,
  `user.admin_on`, `user.admin_off`, Detail `user_id=<id>`.

## Mail-Bericht (Phase 5, ADR 0008)
`app/mail/settings.py` (``MailSettings`` aus `KW_MAIL_*`/`KW_REPORT_*`), `app/mail/transport.py`
(Graph `sendMail`), `app/core/report.py` (Bericht und Versand), `app/web/reports_page.py`,
`app/cli_mail.py`. Spalte `tenant.report_recipients` (Migration `0008`), in der YAML
`report.recipients`. Einrichtung: `docs/mail-setup.md`.

- **Einstellungen:** `Settings.mail` ist `None`, wenn `KW_MAIL_FROM` fehlt. Leere Werte zählen als
  nicht gesetzt (Compose reicht sie durch). `ConfigError` nennt nur den Namen der Variable.
- **Eigener Zugang:** App-Registrierung im Softchem-Tenant, getrennt von den Kunden-Tenants.
  `connectors/graph.py` bleibt rein lesend; der Transport nutzt dessen `MsalCertificateTokenProvider`
  (Zertifikat-Login, privater Schlüssel aus dem Docker-Secret) und schickt selbst
  `POST /users/<postfach>/sendMail` (JSON, `contentType: Text`, `saveToSentItems: false`, erwartet
  202). Der Absender steht URL-kodiert im Pfad.
- **Berechtigung:** nicht in Entra, sondern in Exchange per RBAC for Applications (`Application
  Mail.Send`, Management Scope auf das eine Postfach), siehe `docs/mail-setup.md`.
- **Fehler:** `MailError.code` = `secret`, `auth`, `forbidden` (403), `sender` (404), `rejected` (400),
  `throttled` (429), `connect`, `send`; dazu `graph_code` (technischer Graph-Code, auf
  `[A-Za-z0-9_.:-]` geprüft). Meldungstexte und Originalfehler werden verworfen (`from None`). Keine
  automatische Wiederholung (`sendMail` ist nicht idempotent). Der Betreff verliert Zeilenumbrüche.
- **Anmeldung prüfen:** `check_connection` holt nur ein Token; ob das Postfach senden darf, zeigt der
  Versand.
- **Bericht:** `build_report` nutzt `load_accounts` und `tenant_overview` (dieselben Zahlen wie die
  Übersicht) und rendert einen Text. Enthalten: Konten, markiert, davon lizenziert, freiwerdende
  Lizenzen je Produktname, verschwundene Konten, ablaufende/abgelaufene Ausnahmen, letzter Abruf je
  Quelle, Warnungen, ein Hinweis bei Daten älter als zwei Tage. Nie Konten.
- **Versand:** `send_tenant_report` liest und baut in einer Sitzung, sendet ohne offene Transaktion
  und schreibt danach das Audit-Log (`report.sent` mit `recipients=<n>`, `report.failed` mit
  `error=<code> [graph=<code>]`; Mandant gesetzt, Akteur Benutzername, `cli` oder `system`).
- **Zeitplan:** Job `report` (Cron: `KW_REPORT_WEEKDAY`, `KW_REPORT_TIME`, `KW_TIMEZONE`), nur mit
  Mailversand; `run_weekly_reports` überspringt Mandanten ohne Empfänger und läuft bei Fehlern weiter.
- **Oberfläche (nur Admins):** `GET /reports` (Status, Mandanten mit Empfängerzahl),
  `GET /reports/<kürzel>` (Vorschau), `POST /reports/<kürzel>/send`, `POST /reports/check`
  (Anmeldung prüfen, ohne Mail). Meldungen aus festen Listen.
- **Befehle:** `mail-check`, `report-preview <kürzel>`, `report-send <kürzel>`.

## SKU-Namen (Katalog)
Graph liefert zu einer Lizenz nur die Teilenummer (`skuPartNumber`, z. B. `SPB`). Die lesbaren Namen
stammen aus Microsofts Referenztabelle „Product names and service plan identifiers for licensing“
(CSV). `app/core/sku_catalog.py` macht daraus den Katalog `app/data/sku_catalog.yaml` (617 SKUs), der
mit dem Image ausgeliefert und bei jedem Start in `sku_name` abgeglichen wird; die Quelle und das
Abrufdatum stehen im Kopf der Datei.

- **Regeln:** Leerraum wird entfernt; steht zu einer Teilenummer mehr als ein Name (Umbenennungen,
  z. B. „Power Virtual Agent“ → „Microsoft Copilot Studio“), gilt der **erste** Name der Datei (der
  neuere). Nummern über 100 oder Namen über 200 Zeichen (zwei US-Behörden-Pakete) werden übersprungen.
- **Kostenlos** (`is_free`, zählt nicht als Lizenz und fehlt in „freiwerdende Lizenzen“): Name enthält
  „Free“ als Wort, Nummer endet auf `_FREE` oder `_VIRAL` (Selbstbedienungs-Testversionen) oder ist
  `POWER_BI_STANDARD` (Microsoft Fabric Free). Das sind 9 SKUs; alles andere gilt als bezahlt.
- **Eigene Korrekturen:** `config/sku_names.yaml` hat Vorrang (`free: true|false` erzwingt die
  Einstufung). Unbekannte SKUs erscheinen mit der Teilenummer.
- **Anzeige:** Name, im Kontodetail zusätzlich die Teilenummer klein daneben.
- **Neu erzeugen** (z. B. wenn Microsoft neue SKUs ergänzt), auf einem Entwicklungsrechner:

```bash
curl -o sku.csv "<CATALOG_URL aus app/core/sku_catalog.py>"
python -m app.cli build-sku-catalog sku.csv app/data/sku_catalog.yaml
```

Danach prüft ein Test, dass die Datei gültig, sortiert und ohne Dubletten ist.
Die Einstufung „kostenlos“ wirkt auf Anzeige und Filter sofort; gespeicherte Empfehlungen
(„Lizenz entziehen …“) ändern sich mit der nächsten Auswertung (täglicher Lauf, „Jetzt abrufen“ oder
`refresh-identities`).

## Sicherung der Datenbank (Phase 5)
`app/core/backup.py`, Zeitplan-Job in `core/scheduler.py`, CLI `backup`, `backup-list`,
`backup-verify`, `restore-backup`, Anzeige auf Übersicht und Läufe.

- **Sicherung:** Online-Backup-API von SQLite (`sqlite3.Connection.backup`): konsistent, auch bei
  laufenden Schreibzugriffen (WAL; frische Änderungen aus dem WAL sind enthalten, getestet). Die
  Sicherung wird auf einen eigenständigen Dateimodus umgestellt (`journal_mode=DELETE`), damit sie
  genau **eine** Datei ist. Sie entsteht als `<name>.tmp`, wird geprüft (`PRAGMA integrity_check`,
  Tabellen `alembic_version`, `tenant`, `audit_log`), erhält Modus 0600 und wird erst dann per
  `os.replace` umbenannt. Bei jedem Fehler bleibt keine Teildatei (auch keine `-wal`/`-shm`) zurück.
  Name `kontenwaechter-<JJJJMMTT>-<HHMMSS>.db` (UTC), vor Migrationen `vor-migration-….db`.
- **Zeitplan:** zweiter Cron-Job `backup` um `KW_BACKUP_TIME` (Standard 02:30, eine halbe Stunde vor
  dem Abruf), `max_instances=1`, `coalesce`. `run_backup` wirft nie: Ergebnis als Audit-Eintrag
  `backup.ok` (Datei, Größe, Zahl gelöschter alter) oder `backup.failed` (nur fester Code), Akteur
  `system`, `cli` oder der Benutzername beim Knopf „Jetzt sichern“.
- **Aufbewahrung:** nach der Sicherung werden Dateien mit den eigenen Namensmustern gelöscht, die
  älter als `KW_BACKUP_KEEP_DAYS` (14) sind; die neueste bleibt immer. Fremde Dateien im Ordner
  bleiben unberührt.
- **Vor Migrationen:** Beim Start sichert `backup_before_migration`, wenn die Datenbankdatei existiert
  und nicht auf dem neuesten Stand steht (`alembic_version` ≠ `head`). Scheitert das, startet der
  Dienst trotzdem (eine falsch eingerichtete Sicherung soll ihn nicht blockieren), mit Fehlerzeile
  im Log. Neue Datenbanken werden nicht gesichert.
- **Zustand** (`backup_status`): `ok`, `never` (noch keine), `stale` (älter als
  `KW_BACKUP_WARN_HOURS`, 36), `failed` (Fehlschlag neuer als die letzte gelungene Sicherung),
  `unsupported` (keine SQLite-Datenbank). Sicherungen vor Migrationen zählen nicht als nächtliche.
  Die Übersicht zeigt bei `never`, `stale` und `failed` eine Warnung, sonst eine ruhige Zeile; die
  Läufe-Seite listet die letzten 10 Dateien und hat den Knopf „Jetzt sichern“ (`POST
  /backup/start`, CSRF-geschützt).
- **Wiederherstellung** (`restore_backup`, Anwendung vorher stoppen): prüft die Sicherung, legt die
  bisherige Datei als `<db>.vor-restore-<Zeit>` beiseite, entfernt veraltete `-wal`/`-shm`, kopiert
  atomar und prüft das Ergebnis. Bei einer defekten Sicherung bleibt die Datenbank unverändert. Die
  CLI verlangt `--yes`. Beim nächsten Start laufen fehlende Migrationen.
- **Grenzen:** Das Verzeichnis liegt standardmäßig auf demselben Gerät (KI-6); die Dateien sind
  unverschlüsselt und enthalten Namen und Passwort-Hashes (KI-30); nur SQLite wird gesichert.

## Export (Phase 4, Etappe 4)
`app/export/columns.py` (Spalten, gemeinsam), `csv.py`, `xlsx.py`; Route `app/web/export.py`.
Exportiert wird immer die aktuelle Filteransicht, die ganze Ansicht (nicht nur die Seite).

- **Spalten:** Mandant, Name, Konto (UPN), Anmeldename, E-Mail, Kontoart, Status, Geflaggt, Letzte
  Anmeldung (Maximum aller Quellen), Tage seit Anmeldung, Letzte Anmeldung Entra, Letzte Anmeldung
  AD, Quelle, Lizenzen (nur nicht kostenlose), Empfehlung, Ausnahme bis, Erstellt am.
- **CSV:** Trennzeichen `;`, UTF-8 mit BOM, Zeilenende CRLF, Datum `TT.MM.JJJJ HH:MM` in
  `KW_TIMEZONE`; öffnet in Excel direkt mit Umlauten. Felder mit `;`, `"` oder Zeilenumbruch werden
  gequotet.
- **XLSX** (openpyxl): Blatt „Konten“ als Excel-Tabelle (Stil `TableStyleMedium2`, Filter, fixierte
  Kopfzeile, Spaltenbreiten, farbiger Status, echte Datumszellen `DD.MM.YYYY HH:MM` in Ortszeit,
  Tage als Zahl); „Lizenzen“: nicht kostenlose Lizenzen geflaggter Konten der Ansicht je Mandant und
  SKU; „Info“: Stichtag, Zeitzone, Anzahl, Schwellwerte je Mandant, Filter. Mit LibreOffice
  gegengeprüft (öffnet ohne Fehler).
- **Dateiname:** `Inaktive_Konten_<Mandant>_<JJJJ-MM-TT>.<csv|xlsx>` (`Alle` bei mehreren Mandanten;
  der Mandantenname wird auf `A-Z a-z 0-9 _ -` reduziert).
- **Formel-Injection:** Text, der mit `=`, `+`, `-`, `@`, Tab oder Zeilenumbruch beginnt, bekommt ein
  `'` vorangestellt (CSV und XLSX). Sonst könnte ein Konto mit dem Namen `=HYPERLINK(…)` in Excel
  Formeln ausführen.
- **Audit-Log:** `export.csv|xlsx` mit Format, Zeilenzahl und den gewählten Filtern, nie der Suchtext.
  Die Route verlangt Same-Origin (auch als GET), damit keine fremde Seite Exporte auslösen kann.
- Nicht umgesetzt: `GET /api/accounts` mit API-Token für Skripte (KI-27).

## Zeitplan, Zyklus und Bereinigung (Phase 4, Etappe 2b)
`app/core/scheduler.py` (APScheduler 3.x, `BackgroundScheduler` im App-Prozess), `cycle.py`,
`retention.py`. Der Zeitplan startet in der Lifespan der App, wenn `KW_SCHEDULER_ENABLED` an ist
(Standard im Container; beim direkten Erzeugen von `Settings` aus, damit Tests keinen starten).

- **Auslöser:** ein Cron-Job täglich um `KW_SYNC_TIME` in `KW_TIMEZONE` (Standard 03:00
  Europe/Berlin; `tzdata` gehört zu den Abhängigkeiten, das Slim-Image hat keine Zeitzonendaten).
  `max_instances=1`, `coalesce=True`, `misfire_grace_time` 1 Stunde: Nach einem Ausfall läuft er
  höchstens einmal nach, nie gestapelt.
- **Zyklus je Mandant** (`run_tenant_cycle`): konfigurierte Quellen nacheinander (Entra, dann AD),
  danach `refresh_identities`, auch wenn eine Quelle scheiterte (die Auswertung nimmt je Quelle den
  jüngsten brauchbaren Lauf). Ein Fehler (auch ein unerwarteter) stoppt weder die andere Quelle noch
  andere Mandanten; im Ergebnis stehen nur Statuscodes (`success`, `partial`, `failed`, `busy`,
  `skipped`, `error`). Läuft für den Mandanten schon ein Zyklus, wird er übersprungen. Zusätzlich
  sperren die Abrufe je (Mandant, Quelle) und die Auswertung je Mandant.
- **Nächtlicher Lauf** (`run_nightly`): `run_all` (alle aktiven Mandanten alphabetisch; läuft schon
  einer, wird übersprungen), danach die Bereinigung.
- **Bereinigung** (`purge_raw_data`): Konten (`entra_account`, `ad_account`) und Lizenzbestände
  (`tenant_sku`) von Läufen, die älter als `KW_RETENTION_DAYS` sind. Der jüngste brauchbare Lauf je
  (Mandant, Quelle) bleibt immer erhalten, auch wenn er alt ist; die Läufe (`sync_run`) bleiben als
  Protokoll. Verweise aus `identity` auf gelöschte Konten werden `NULL`.
- Befehle: `python -m app.cli cycle <kürzel>` und `purge`.

## Ausnahmen (Phase 4, Etappe 2b)
`app/core/exceptions.py`. Eine Ausnahme braucht einen Grund (höchstens 500 Zeichen) und ein
Ablaufdatum in der Zukunft; der Standard ist jetzt plus `thresholds.exception_days` des Mandanten
(365). Je Identität gibt es höchstens eine gültige Ausnahme (`already_valid`); eine abgelaufene kann
durch eine neue ersetzt werden. `list_exceptions` sortiert nach Ablauf und markiert abgelaufene und
bald ablaufende (`thresholds.exception_warn_days`, Standard 30). Anlegen und Löschen schreiben
`audit_log` (`exception.create|delete`, nur IDs und Ablaufdatum, nie Grund oder Namen) und lösen die
Auswertung des Mandanten aus, damit Status und Flag sofort stimmen. Jede Abfrage filtert nach
`tenant_id`. Die Seiten dazu sind in „Oberfläche“ beschrieben.

## Noch nicht vorhanden
Reverse-Proxy mit HTTPS und Anmeldung per Entra ID (Phase 5). Nicht umgesetzt: Mandantenseite (Mandanten stehen in der YAML), Warnung vor ablaufendem Zertifikat, Verlauf je Konto (KI-24 bis KI-26).
