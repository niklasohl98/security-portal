# Konfigurationsreferenz

Diese Seite ist die vollständige Referenz der Laufzeitkonfiguration. Einrichtungsabläufe stehen in
den verlinkten Anleitungen; nicht geheime Beispielwerte in [`.env.example`](../.env.example) und
[`config/tenants.example.yaml`](../config/tenants.example.yaml).

## Quellen und Priorität

Prozessweite Einstellungen kommen aus `KW_*`-Umgebungsvariablen. Nicht gesetzte Variablen verwenden
die unten genannten Defaults. Mandanten stehen in `/config/tenants.yaml`. Secret-Felder enthalten
ausschließlich den Namen einer Datei unter `KW_SECRETS_DIR`, nie den Secret-Wert. Unbekannte
YAML-Schlüssel und ungültige Werte brechen den Start mit einer bereinigten Fehlermeldung ab.

`docker-compose.yml` reicht die Variablen aus einer lokalen `.env` weiter. Diese Datei und die
Laufzeitkonfiguration werden nicht committed.

## Allgemein und Zeitplan

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_DATA_DIR` | `/data` | Beschreibbares Verzeichnis für SQLite und Laufdaten. |
| `KW_DATABASE_URL` | `sqlite:///<KW_DATA_DIR>/kontenwaechter.db` | SQLAlchemy-URL. Sicherungs- und Migrationsfunktionen setzen SQLite voraus. |
| `KW_TENANTS_FILE` | `/config/tenants.yaml` | Pfad der Mandantenkonfiguration. |
| `KW_SECRETS_DIR` | `/run/secrets` | Verzeichnis für eingehängte Secret-Dateien. |
| `KW_SKU_NAMES_FILE` | `/config/sku_names.yaml` | Optionale Korrekturen am mitgelieferten SKU-Katalog. |
| `KW_LOG_LEVEL` | `INFO` | Log-Stufe, üblich sind `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs sind JSON auf stdout. |
| `KW_SCHEDULER_ENABLED` | `1` | `0`, `false`, `no` oder leer deaktiviert alle Scheduler-Jobs. |
| `KW_SYNC_TIME` | `03:00` | Täglicher Mandantenlauf als `HH:MM` in `KW_TIMEZONE`. |
| `KW_TIMEZONE` | `Europe/Berlin` | Gültige IANA-Zeitzone für Jobs, Oberfläche und Export. Datenbankwerte bleiben UTC. |
| `KW_RETENTION_DAYS` | `90` | Positive Zahl; Aufbewahrung alter Rohläufe, jüngster brauchbarer Lauf bleibt. |

## Anmeldung, HTTPS und Zertifikate

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_SESSION_HOURS` | `8` | Positive Zahl; absolute Lebensdauer einer Sitzung. |
| `KW_LOGIN_MAX_ATTEMPTS` | `5` | Positive Zahl; Fehlversuche bis zur zeitweiligen Sperre. |
| `KW_LOGIN_LOCKOUT_MINUTES` | `15` | Positive Zahl; Dauer der Sperre. |
| `KW_COOKIE_SECURE` | `auto` | `auto`, `1`/`true` oder `0`/`false`. Mit `KW_PUBLIC_URL` darf der Wert nicht aus sein. |
| `KW_PUBLIC_URL` | leer | Kanonische HTTPS-Origin ohne Pfad, Query oder Fragment, z. B. `https://accounts.softchem.netz`. Aktiviert Host-/HTTPS-Prüfung und HSTS. |
| `KW_TRUSTED_PROXY_IPS` | `127.0.0.1` | Kommagetrennte konkrete IPs/CIDRs für Uvicorn-Proxy-Header; `*` und `/0` sind verboten. |
| `KW_CERTIFICATES_FILE` | `/config/certificates.yaml` | Optionales Inventar öffentlicher PEM-/DER-Zertifikate für Ablaufwarnungen. |

DSM-Proxy, DNS und Zertifikat: [HTTPS-Anleitung](https-setup.md). Das Zertifikatsinventar enthält
keine privaten Schlüssel und ersetzt keine Prüfung der tatsächlichen TLS-Verbindung.

## Entra-SSO

SSO ist vollständig opt-in. Bei `KW_SSO_ENABLED=0` werden unvollständige Platzhalter ignoriert und
keine SSO-Verbindung aufgebaut. Einrichtung und Sicherheitsgrenzen: [SSO-Anleitung](sso-setup.md).

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_SSO_ENABLED` | `0` | Nur `1`/`true` aktiviert SSO; benötigt `KW_PUBLIC_URL`. |
| `KW_SSO_TENANT_ID` | leer | GUID des einzelnen zugelassenen Softchem-Tenants. |
| `KW_SSO_CLIENT_ID` | leer | GUID der App-Registrierung; darf der Mail-App entsprechen. |
| `KW_SSO_ALLOWED_GROUP_ID` | leer | GUID der Sicherheitsgruppe, deren transitive Mitglieder Zugang erhalten. |
| `KW_SSO_ADMIN_GROUP_ID` | leer | Optionale GUID einer zusätzlichen Admin-Gruppe. Leer bedeutet keine SSO-Admins. |
| `KW_SSO_CERT_SECRET` | leer | Name des Docker-Secrets mit dem privaten PEM-Schlüssel. |
| `KW_SSO_CERT_THUMBPRINT` | leer | SHA-1-Fingerabdruck des App-Zertifikats, genau 40 Hex-Zeichen. |
| `KW_SSO_RECHECK_SECONDS` | `300` | Positive Zahl bis 900; Intervall der erneuten Gruppenprüfung. |

## Lokale Sicherung

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_BACKUP_DIR` | `/backup` | Eingehängtes, für UID 10001 beschreibbares Sicherungsverzeichnis. |
| `KW_BACKUP_TIME` | `02:30` | Tägliche Sicherung als `HH:MM` in `KW_TIMEZONE`. |
| `KW_BACKUP_KEEP_DAYS` | `14` | Positive Zahl; Aufbewahrung, wobei die neueste Sicherung immer bleibt. |
| `KW_BACKUP_WARN_HOURS` | `36` | Positive Zahl; ab diesem Alter warnt die Oberfläche. |

Bedienung und Restore: [Betriebsanleitung](operations.md) und [CLI-Referenz](cli.md).

## Externe verschlüsselte Sicherung

Ohne Ziel bleibt `KW_EXTERNAL_BACKUP_ENABLED=0`. Dann gibt es weder Job noch Netzwerkzugriff oder
Warnung. Zielwahl, Secret-Dateien, Host-Key-Prüfung und Restore stehen in der
[Backup-Anleitung](external-backup.md).

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_EXTERNAL_BACKUP_ENABLED` | `0` | Nur `1`/`true` aktiviert Ziel und Scheduler-Job. |
| `KW_EXTERNAL_BACKUP_REPOSITORY` | leer | `rest:https://…`, `s3:https://…` oder `sftp:[user@]host:/pfad`; keine Zugangsdaten in URLs. |
| `KW_EXTERNAL_BACKUP_PASSWORD_SECRET` | leer | Erforderlicher Secret-Name für die Restic-Verschlüsselung. |
| `KW_EXTERNAL_BACKUP_CREDENTIALS_SECRET` | leer | Optionaler Secret-Name mit erlaubten AWS-/REST-Zugangsdaten. |
| `KW_EXTERNAL_BACKUP_SSH_SECRET` | leer | Für SFTP erforderlich; Secret-Name des privaten SSH-Schlüssels. |
| `KW_EXTERNAL_BACKUP_CA_FILE` | leer | Optionale öffentliche CA, relativ zum Ordner der `tenants.yaml`, ohne `..`. |
| `KW_EXTERNAL_BACKUP_TIME` | `02:45` | Täglicher Lauf als `HH:MM` in `KW_TIMEZONE`. |
| `KW_EXTERNAL_BACKUP_TIMEOUT_SECONDS` | `1800` | Positive Zahl bis 7200; beendet auch SSH-Kindprozesse. |
| `KW_EXTERNAL_BACKUP_WARN_HOURS` | `36` | Positive Zahl; ab diesem Alter gilt das Ziel als überfällig. |
| `KW_EXTERNAL_BACKUP_KEEP_DAILY` | `14` | Positive Zahl täglicher Snapshots für den bestätigten Prune. |
| `KW_EXTERNAL_BACKUP_KEEP_WEEKLY` | `8` | Positive Zahl wöchentlicher Snapshots. |
| `KW_EXTERNAL_BACKUP_KEEP_MONTHLY` | `12` | Positive Zahl monatlicher Snapshots. |

## Mail-Bericht

Fehlt `KW_MAIL_FROM`, ist Mail vollständig aus. Die übrigen Platzhalter dürfen dann leer bleiben.
Einrichtung und Exchange-RBAC: [Mail-Anleitung](mail-setup.md).

| Variable | Standard | Format und Wirkung |
|---|---|---|
| `KW_MAIL_FROM` | leer | Gültige Absenderadresse; aktiviert Mailversand und Berichtsjob. |
| `KW_MAIL_TENANT_ID` | leer | GUID des Softchem-Tenants der Mail-App. |
| `KW_MAIL_CLIENT_ID` | leer | GUID der Mail-App. |
| `KW_MAIL_CERT_SECRET` | leer | Name des Docker-Secrets mit dem privaten PEM-Schlüssel. |
| `KW_MAIL_CERT_THUMBPRINT` | leer | SHA-1-Fingerabdruck, genau 40 Hex-Zeichen. |
| `KW_REPORT_WEEKDAY` | `mon` | `mon` bis `sun`; Wochentag des Berichts. |
| `KW_REPORT_TIME` | `07:30` | `HH:MM` in `KW_TIMEZONE`. |

## Mandantendatei

Die oberste Ebene ist `tenants:` mit einer Liste. `slug` muss eindeutig sein und aus Kleinbuchstaben,
Ziffern und Bindestrichen bestehen. Jeder Mandant braucht mindestens `entra` oder `ad`.

| Feld | Standard | Bedeutung |
|---|---|---|
| `slug` | erforderlich | Stabiles technisches Kürzel, höchstens 32 Zeichen. |
| `name` | erforderlich | Anzeigename. |
| `enabled` | `true` | Deaktivierte Mandanten werden nicht abgerufen. Entfernte YAML-Einträge werden in der DB deaktiviert, nicht gelöscht. |
| `entra.tenant_id`, `entra.client_id` | erforderlich für Entra | GUIDs der Kunden-App. |
| `entra.certificate_secret` | erforderlich für Entra | Secret-Name des privaten Schlüssels. |
| `entra.certificate_thumbprint` | erforderlich für Entra | 40-stelliger SHA-1-Fingerabdruck. |
| `ad.domain` | erforderlich für AD | DNS-Domäne. |
| `ad.dcs` | `[]` | Feste DC-Hostnamen; leer verwendet DNS-SRV. |
| `ad.bind_user`, `ad.bind_secret` | erforderlich für AD | Lesekonto und Secret-Name seines Passworts. |
| `ad.ca_file` | leer | Öffentliche CA relativ zum Konfigurationsordner. |
| `ad.accept_weak_dc_keys_until` | leer | Befristetes Datum für die in ADR 0005 beschriebene Ausnahme. |
| `thresholds.inactive_days` | `30` | Schwelle für inaktiv. |
| `thresholds.long_inactive_days` | `90` | Muss größer als `inactive_days` sein. |
| `thresholds.new_account_grace_days` | `14` | Schonfrist neuer Konten, darf 0 sein. |
| `thresholds.exception_days` | `365` | Vorgeschlagene Laufzeit einer Ausnahme. |
| `thresholds.exception_warn_days` | `30` | Vorlauf der Ablaufwarnung, darf 0 sein. |
| `account_patterns.<art>` | nur `admin: ["*adm*"]` | Groß-/kleinschreibungsunabhängige `*`-/`?`-Muster für `admin`, `service`, `test`, `resource`. |
| `report.recipients` | `[]` | Bis zu 20 eindeutige Empfänger; leer überspringt den Mandantenbericht. |

## Zertifikatsinventar

`certificates.yaml` enthält `certificates:` als Liste. Jeder Eintrag hat `label` (technischer Name),
`file` (öffentliche PEM-/DER-Datei relativ zum Inventar) und optional `warn_days` (Standard 30,
1 bis 365). Absolute Pfade, `..`, doppelte Labels und Dateien mit `PRIVATE KEY` werden
abgelehnt. Vorlage: [`config/certificates.example.yaml`](../config/certificates.example.yaml).
