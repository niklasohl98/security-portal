# CLI-Referenz

Alle Befehle laufen im App-Container mit derselben Konfiguration wie der Dienst:

```bash
docker compose exec app python -m app.cli <befehl>
```

`--help` zeigt die verfügbaren Befehle. Erfolg liefert Exitcode 0, ein fachlicher oder technischer
Fehler 1, fehlende Bestätigung beziehungsweise deaktivierte Funktion 2. Ausgaben enthalten keine
Secret-Werte oder externen Fehlermeldungen.

## Mandanten und Abrufe

| Befehl | Wirkung |
|---|---|
| `tenants` | Konfigurierte Mandanten auflisten. |
| `check-entra <slug>` | Zertifikat, Anmeldung und lesenden Graph-Zugriff prüfen; speichert keine Konten. |
| `sync-entra <slug>` | Entra-Konten und Lizenzen abrufen und als Lauf speichern. |
| `check-ad <slug>` | LDAPS, DC-Erkennung, Zertifikate und Lesbarkeit prüfen; speichert keine Konten. |
| `sync-ad <slug>` | AD-Konten und Anmeldedaten aller erreichbaren DCs abrufen. |
| `refresh-identities <slug>` | Jüngste Rohläufe zusammenführen und Status/Empfehlungen neu berechnen. |
| `cycle <slug>` | Entra und AD abrufen, auswerten und alte Rohläufe bereinigen; entspricht dem Tagesjob. |
| `purge` | Alte Rohläufe nach `KW_RETENTION_DAYS` bereinigen; jüngste brauchbare Läufe bleiben. |

`check-*` ist die sichere Erstprüfung nach einer Konfigurationsänderung. `sync-*` und `cycle`
schreiben nur in die lokale Datenbank; Kunden-Entra und AD bleiben lesend.

## Tool-Benutzer

| Befehl | Wirkung |
|---|---|
| `user-list` | Lokale und externe Tool-Benutzer mit Status/Rolle auflisten. |
| `user-add <username> [--admin]` | Lokalen Benutzer anlegen; Passwort wird verdeckt zweimal abgefragt und muss beim ersten Login geändert werden. |
| `user-reset <username>` | Lokales Passwort neu setzen, Konto entsperren/aktivieren und bestehende Sitzungen beenden. |

SSO-Benutzer besitzen kein lokales Passwort und lassen sich daher nicht per `user-reset` übernehmen.
Mindestens ein aktiver lokaler Admin bleibt als Notzugang erhalten.

## Lokale Datenbanksicherung

| Befehl | Wirkung |
|---|---|
| `backup` | Sofort konsistente SQLite-Sicherung erstellen, prüfen und Aufbewahrung anwenden. |
| `backup-list` | Erkannte Sicherungen nach Zeit sortiert auflisten. |
| `backup-verify <datei>` | Integrität und erwartete Tabellen einer Sicherung prüfen. |
| `restore-backup <datei> --yes` | Sicherung in die konfigurierte SQLite-Datenbank übernehmen; alte Sitzungen werden verworfen. |

Vor `restore-backup` den App-Dienst stoppen. Die bisherige Datenbank wird mit Zeitstempel beiseite
gelegt; bei einer ungültigen Sicherung bleibt sie unverändert. Vollständiger Ablauf:
[Betriebsanleitung](operations.md#update-und-rückfall).

## Externe verschlüsselte Sicherung

Diese Befehle funktionieren nur mit `KW_EXTERNAL_BACKUP_ENABLED=1`. Sie initialisieren oder löschen
nichts automatisch.

| Befehl | Wirkung |
|---|---|
| `external-backup-init` | Leeres Restic-Repository einmalig initialisieren. |
| `external-backup-run` | Geprüftes DB-/Konfigurationspaket ohne Secrets verschlüsselt sichern. |
| `external-backup-check` | Repository und sämtliche Daten mit `--read-data` prüfen. |
| `external-backup-restore <neues-verzeichnis> [--snapshot <id|latest>]` | Snapshot ausschließlich in ein noch nicht vorhandenes Verzeichnis zurückholen und Paket prüfen. |
| `external-backup-prune --yes` | Konfigurierte tägliche/wöchentliche/monatliche Aufbewahrung anwenden und Daten löschen. |

Der externe Restore ersetzt niemals die laufende Datenbank. Prüfung, Secret-Recovery und bewusste
Übernahme stehen in der [Backup-Anleitung](external-backup.md).

## Berichte und Wartung

| Befehl | Wirkung |
|---|---|
| `mail-check` | Zertifikat und Tokenbezug der Mail-App prüfen; sendet keine Nachricht. |
| `report-preview <slug>` | Wochenbericht als Text erzeugen; sendet nichts. |
| `report-send <slug>` | Bericht für einen Mandanten sofort an konfigurierte Empfänger senden. |
| `build-sku-catalog <csv> <ausgabe>` | Microsoft-CSV in den versionierten SKU-Katalog umwandeln; Entwicklungs-/Wartungsbefehl. |

`mail-check` belegt noch nicht die Exchange-Sendeberechtigung. Diese wird erst mit einem gezielten
`report-send` und der Kontrolle des Audit-Eintrags abgenommen.
