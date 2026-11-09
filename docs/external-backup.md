# Externe verschlüsselte Sicherung

Phase 5.3. Es gibt derzeit noch kein zweites Sicherungsziel. Nach Vorgabe von Niklas ist die
Funktion **vorbereitet und standardmäßig deaktiviert**. Lokale Sicherungen laufen unverändert
weiter. Ohne `KW_EXTERNAL_BACKUP_ENABLED=1` gibt es keinen externen Zeitplan-Job, keinen
Verbindungsversuch und keine fehlende-Ziel-Warnung. Alle Zugangsdaten in diesem Dokument sind
Platzhalter oder Secret-Namen.

## Umfang

Restic verschlüsselt Inhalt und Metadaten des externen Repositorys. Ein Lauf erzeugt eine
konsistente, geprüfte SQLite-Kopie und ein Wiederherstellungspaket mit Mandanten-YAML,
gegebenenfalls eigenen SKU-Namen, referenzierten AD-/Backup-CA-Dateien und SSH-known_hosts.
Ein Manifest nennt Format, Zeitpunkt und benötigten Schema-Stand. Private Schlüssel/Secret-Werte
werden nicht aufgenommen; versehentliche private Schlüssel in Konfigurationsdateien werden
abgelehnt. Temporäre Dateien liegen in einem privaten Verzeichnis und werden nach dem Lauf entfernt.

Vorgesehene Ziele:

| Ziel | Repository-Format | Zugang |
|---|---|---|
| Restic REST Server | `rest:https://<server>/<repository>/` | optionale REST-Credentials als Docker-Secret |
| S3-kompatibler Speicher | `s3:https://<endpoint>/<bucket>/<prefix>` | AWS-Credentials als Docker-Secret |
| Zweites NAS mit SFTP | `sftp:<user>@<host>:/<repository>` | SSH-Key als Docker-Secret, geprüfte Hostkeys |

Je Instanz ein eigenes Repository/Prefix verwenden. Repository darf nie auf derselben Synology
liegen; ein lokales Dateiziel ist in der Deploymentkonfiguration nicht zugelassen. HTTPS-
Zertifikate bzw. SSH-Hostkeys werden immer geprüft. Kein HTTP, kein `--insecure-tls`, kein
`StrictHostKeyChecking=no`. Ein Restic-Repository ist kein allgemeines S3-/SFTP-Verzeichnis.

## Spätere Einrichtung

1. Ziel, Zugriffsrechte, Kapazität und getrennte Wiederherstellungsverantwortliche festlegen.
   Wenn der Anbieter es unterstützt: unveränderbare/append-only Sicherung und getrennte
   Berechtigung für Aufbewahrung/Löschung einrichten.
2. Starkes zufälliges Repository-Passwort und benötigte Ziel-Credentials im eigenen Secret-
   Verfahren erzeugen. Nur Secret-Namen in der `.env`, Werte als Docker-Secrets einhängen.
   Schlüssel und Repository-Passwort zusätzlich getrennt vom NAS im geschützten Notfallzugang
   verwahren; ohne sie ist kein Restore möglich. Die Token-Caches von SSO gehören nicht ins Backup.
3. Nicht-geheime Konfiguration, lokale Compose-Override-Datei, `.env` und Deployment-/Image-Stand
   ebenfalls im geschützten Betriebshandbuch sichern. Das Paket enthält keine gesamte DSM-
   Konfiguration und keinen automatisch wiederherstellbaren Docker-Secret-Bestand.
4. Erst nach Konfiguration aktivieren und initialisieren; kein automatisches `init` beim Start.

Beispiel `.env` (alle Platzhalter ersetzen):

```dotenv
KW_EXTERNAL_BACKUP_ENABLED=1
KW_EXTERNAL_BACKUP_REPOSITORY=rest:https://<backup-server>/<repository>/
KW_EXTERNAL_BACKUP_PASSWORD_SECRET=external_backup_password
KW_EXTERNAL_BACKUP_CREDENTIALS_SECRET=external_backup_credentials
KW_EXTERNAL_BACKUP_TIME=02:45
KW_EXTERNAL_BACKUP_WARN_HOURS=36
```

Die Credentials-Secret-Datei erlaubt ausschließlich `KEY=VALUE`-Zeilen für:
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION`,
`RESTIC_REST_USERNAME`, `RESTIC_REST_PASSWORD`. Keine Shell-Auswertung, keine Anführungszeichen
zur Maskierung, keine Passwörter im Repository-URL. Leere/Kommentarzeilen sind erlaubt.
Für einen Server ohne separate Credentials den zugehörigen Eintrag leer lassen.

SFTP zusätzlich: `KW_EXTERNAL_BACKUP_SSH_SECRET=<ssh-secret-name>` und verifizierte öffentliche
Hostkeys nach `config/backup_known_hosts`. Fingerabdrücke über einen unabhängigen Kanal prüfen;
ein ungeprüftes `ssh-keyscan` allein stellt kein Vertrauen her. Secret-Dateien müssen für UID
10001 lesbar sein, private SSH-Schlüssel mit Modus 0400/0600. Bei interner HTTPS-CA optional
`KW_EXTERNAL_BACKUP_CA_FILE=ca/backup.pem` setzen; Datei unter `config/ca/` schreibgeschützt einhängen.

```bash
docker compose up -d --build
docker compose exec app python -m app.cli external-backup-init
docker compose exec app python -m app.cli external-backup-run
docker compose exec app python -m app.cli external-backup-check
```

Nach erfolgreicher Einrichtung läuft täglich um 02:45 (`KW_TIMEZONE`) ein eigener Job mit frischer
SQLite-Kopie, unabhängig von den lokalen Sicherungsdateien. Unter **Läufe** stehen Zustand,
letzter Erfolg, letzte Integritätsprüfung und letzter erfolgreicher Rückholtest; Admins können
einen Lauf starten. Fehler und Überschreitung der Warnfrist erscheinen auf der Übersicht.
Details stehen als feste Codes im Audit-Log, nie als rohe Restic-Ausgaben oder Zieladressen.
Status gehört zum konfigurierten Ziel; ein geändertes Ziel beginnt mit „noch nie gesichert“.

## Restore ohne Überschreiben des Livebetriebs

Zuerst in einen **neuen, noch nicht vorhandenen** Ordner im beschreibbaren Datenvolume holen:

```bash
docker compose exec app python -m app.cli external-backup-restore /data/restore-test
```

Optional `--snapshot <snapshot-id>` statt `latest`. Der Befehl filtert auf die eigenen
Kontenwächter-Snapshots, entpackt nur reguläre Dateien mit erlaubten Pfaden und prüft SQLite und
Mandantenkonfiguration. Eine fehlerhafte Rückholung ersetzt nie die laufende Datenbank.
Das Manifest und `config/` liegen neben `database.db` im Zielordner. Erneuter Test: neues Ziel
verwenden, vorhandene Ordner werden nicht überschrieben.

Für einen echten Wiederanlauf auf einem getrennten Ziel:

1. App-Image/Compose, nicht-geheime Konfiguration, Secret- und Repository-Zugänge bereitstellen.
2. Paket zurückholen und Integritätsprüfung durchführen; passende Software-/Schema-Version nutzen.
3. App stoppen. `restore-backup <zurückgeholte-database.db> --yes` aus der bestehenden CLI verwenden.
   Die alte Datenbank bleibt als Rückfall erhalten. `config/` bewusst an den vorgesehenen Ort
   übernehmen, Secret-Referenzen abgleichen und fehlende Secrets getrennt bereitstellen.
4. App starten; Benutzer, Ausnahmen, Entscheidungen, letzte Datenstände und lokale Anmeldung prüfen.
   Der Restore verwirft alle alten Tool-Sitzungen; lokale und SSO-Benutzer müssen neu anmelden.
   Ergebnis und Zeiten dokumentieren.

**Abnahme:** Ziel physisch getrennt, Sicherung und vollständiger `check --read-data` erfolgreich,
Restore samt fachlichem Prüflauf auf getrenntem Testsystem, Verlust-/Zeitfenster gemessen.
Vorgeschlagene Ziele: RPO maximal 24 Stunden, RTO maximal 4 Stunden; durch Niklas noch festzulegen
und am gewählten Ziel zu bestätigen. Ein lokaler Integrationstest ersetzt diese Live-Abnahme nicht.

## Aufbewahrung, Fehler und Abschalten

Standardvorschlag: 14 tägliche, 8 wöchentliche, 12 monatliche Stände. Es wird **nicht automatisch
gelöscht**. Zuerst Restore nachweisen und Aufbewahrung am Ziel abstimmen, dann ausdrücklich:

```bash
docker compose exec app python -m app.cli external-backup-prune --yes
```

Die Aktion betrifft nur die eigenen Tags/den festen Kontenwächter-Host im dedizierten Repository.
Objekt-Lock/append-only Ziele können die Löschung ablehnen; hierzu getrennte Wartungszugänge
verwenden, nicht den täglichen Backup-Zugang erweitern. Kein anderer Datenbestand gehört in
dieses Repository. Zeitlimit pro Aktion: 1800 Sekunden, konfigurierbar bis maximal 7200.
Auch unvollständige Restic-Läufe gelten als Fehler; kein stilles Fortsetzen nach Teilfehlern.

Zum Abschalten `KW_EXTERNAL_BACKUP_ENABLED=0` setzen und den Container neu erstellen. Lokale
Sicherungen laufen weiter, vorhandene externe Snapshots bleiben erhalten. Typische Fehlercodes:
`secret`, `credentials`, `ssh_hosts`, `bundle`, `restic`, `timeout`, `busy`, `target`, `restore`.
Ein verschlüsseltes Repository schützt Vertraulichkeit; passende Zielrechte und getrennte
Zugänge schützen zusätzlich vor Löschung durch kompromittierte tägliche Backup-Credentials.

## Quellen

- [Restic: Ziele und Repository-Passwort](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html)
- [Restic: Wiederherstellung](https://restic.readthedocs.io/en/stable/050_restore.html)
- [Restic: Aufbewahrung](https://restic.readthedocs.io/en/stable/060_forget.html)
