# Architektur (Stand Phase 3)

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

## Noch nicht vorhanden
Merge/Regeln/Scheduler/Export/Oberfläche (Phase 4), Anmeldung, Audit-Log-Nutzung, Backups und
Reverse-Proxy (Phase 5). Entra- und AD-Abruf laufen bisher nur per Befehlszeile; Rohdaten-Bereinigung
nach 90 Tagen fehlt noch.
