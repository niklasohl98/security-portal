# Architektur (Stand Phase 4, Etappe 3)

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
5. Optionale SKU-Namenstabelle (`sku_names.yaml`) in `sku_name` abgleichen
   (`app/core/sku_names.py`); Einträge nur in der DB bleiben erhalten
6. Unterbrochene Läufe (`running`) als `failed` abschließen (`app/core/sync.py`)

`GET /health` prüft die Datenbank (`SELECT 1`): 200 `{"status":"ok"}` oder 503. Er gibt keine
Mandantendaten preis. OpenAPI und Doku-Seiten sind abgeschaltet.

## Konfiguration
Umgebungsvariablen `KW_*` (siehe README) und `config/tenants.yaml`. Secrets werden nur über
`read_secret(name, secrets_dir)` aus `/run/secrets/` gelesen; der Name ist auf `[A-Za-z0-9._-]`
beschränkt (kein Pfadausbruch). In der YAML und in der Tabelle steht nur der Secret-*Name*.

## Datenmodell
`app/db/models.py`. Jede Tabelle außer `tenant` und `sku_name` trägt `tenant_id`. Zeitwerte nutzen
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

Alle Änderungen betreffen nur diese Datenbank und stehen im Audit-Log (nur IDs und Codes, nie Namen
oder Gründe): `identity.keep|delete|reopen`, `exception.create|delete`, `identity.account_type`,
`sync.manual`. Der Akteur ist bis Phase 5 „lokal (ohne Anmeldung)“.

**Filter:** `web/filters.py` liest die Parameter; ungültige Werte werden ignoriert und nie
zurückgegeben. Die Meldungen der Seiten (`?done=`, `?err=`) stammen aus festen Listen. **Zeiten**
erscheinen in `KW_TIMEZONE` als `TT.MM.JJJJ HH:MM` (Zeitzone im Seitenfuß; `labels.format_dt`).
**Kontoart-Anzeige:** die manuelle Kontoart ersetzt die automatische und trägt das Kennzeichen
„manuell“. „Lizenziert“ heißt: mindestens eine Lizenz, deren SKU nicht als kostenlos gilt.

Schutzmaßnahmen (Anmeldung folgt in Phase 5, KI-1): Antwort-Header (`Content-Security-Policy`
ohne Skripte, `X-Frame-Options: DENY`, `nosniff`, `Cache-Control: no-store` für HTML,
`Referrer-Policy: same-origin`). Nicht `no-referrer`: Browser senden dann bei Formularen
`Origin: null`, und die CSRF-Prüfung lehnte die eigene Seite ab (im Browser geprüft). Jede
ändernde Route (POST) verlangt Same-Origin: `Sec-Fetch-Site` muss `same-origin`/`none` sein und
`Origin` zum `Host` passen; fehlen beide (Skripte), gilt die Anfrage als zulässig. Formulare
werden ohne `python-multipart` gelesen (nur `application/x-www-form-urlencoded`, höchstens 4 KiB,
höchstens 20 Felder). Jinja2 maskiert alle Ausgaben (Autoescape).

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
Export (Phase 4, Etappe 4), Anmeldung, Backups und Reverse-Proxy (Phase 5). Nicht umgesetzt: Mandantenseite (Mandanten stehen in der YAML), Warnung vor ablaufendem Zertifikat, Verlauf je Konto (KI-24 bis KI-26).
