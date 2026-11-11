# Betriebshandbuch

Stand: 11. November 2026. Der Livebetrieb sowie HTTPS und Entra-SSO sind durch Niklas bestätigt.
Das reale Zertifikatsinventar und ein zweites Sicherungsziel bleiben offen. Dieses Handbuch ist
die kanonische Quelle für Überwachung, Wartung und Wiederanlauf.

## Betriebsmodell

| Thema | Einrichtung und Abnahme | Detailanleitung |
|---|---|---|
| Anwendung | Image, private Konfiguration, Secrets, Erreichbarkeit | dieses Handbuch |
| HTTPS | DSM-Proxy, DNS, Zertifikatskette und Proxy-IP | [DSM-HTTPS](https-setup.md) |
| SSO | App-Registrierung, IDs, Gruppen und Secret-Referenz | [Entra-SSO](sso-setup.md) |
| Quellen | Berechtigungen, Zertifikate und Verbindung je Mandant | [Entra](entra-setup.md), [AD](ad-setup.md) |
| Mail | Absender-App, Exchange-Begrenzung und Empfänger | [Mail-Bericht](mail-setup.md) |
| Sicherung | lokales Volume; später unabhängiges, verschlüsseltes Ziel | [externes Backup](external-backup.md) |

Echte Secrets, IDs und Kundendaten gehören ausschließlich in die geschützte
Deploymentkonfiguration. Das Repository enthält nur Vorlagen und erfundene Beispiele.

## Start, Status und Stopp

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/health
docker compose logs --tail=100 app
```

`/health` bestätigt Prozess und Datenbankzugriff. Die fachliche Betriebsbereitschaft ergibt sich
zusätzlich aus **Übersicht** und **Läufe**: aktuelle erfolgreiche Entra-/AD-Läufe, keine ungeklärten
Warnungen, eine frische lokale Sicherung und gültige inventarisierte Zertifikate.

```bash
docker compose stop app
```

Ein kontrollierter Stopp wartet auf laufende Requests und Scheduler-Aufgaben.

## Regelmäßige Kontrollen

| Intervall | Kontrolle | Erfolgskriterium |
|---|---|---|
| täglich | `/health`, Containerstatus, Übersicht und letzte Läufe | Prozess gesund; Quellenläufe erfolgreich oder erklärbar |
| täglich | lokaler Sicherungsstatus | letzte Sicherung jünger als `KW_BACKUP_WARN_HOURS` und verifiziert |
| wöchentlich | Warnungen, offene Prüfungen, Berichtsfehler | bewertet; Fehler behoben oder nachverfolgt |
| monatlich | Speicherplatz, Paketbefunde, Zertifikatsfristen | Reserve vorhanden; Befunde geplant; keine unerwartete Frist |
| quartalsweise | Restore-Probe und Notzugänge | Rückholung auf getrenntem Pfad erfolgreich |
| bei Änderungen | Berechtigungen, Gruppen und Secrets | minimal nötiger Zugriff; abgelehnte Benutzer bleiben abgelehnt |

Hilfreiche Befehle stehen vollständig in der [CLI-Referenz](cli.md). Für eine manuelle Prüfung
eignen sich `tenants`, `check-entra <tenant>`, `check-ad <tenant>`, `backup-list`,
`backup-verify <datei>` und bei eingerichteter Mail-App `mail-check`.

## Zertifikate und CG-Ausnahme

`config/certificates.example.yaml` nach `config/certificates.yaml` kopieren. Öffentliche
PEM-/DER-Zertifikate unter `config/certificates/` hinterlegen und mit technischem Namen,
relativem Dateipfad und `warn_days` (Standard 30) eintragen. Ein anderer Inventarpfad wird über
`KW_CERTIFICATES_FILE` gesetzt. Private Schlüssel bleiben in Docker-Secrets beziehungsweise DSM.

Die Übersicht warnt bei bevorstehendem Ablauf, abgelaufenen, noch nicht gültigen und unlesbaren
Zertifikaten. Fehlendes optionales Inventar bedeutet fehlende Überwachung. Ungültige Struktur oder
Pfade verhindern den Start; eine später fehlende einzelne Datei erscheint als Warnung. Geprüft wird
die zeitliche Gültigkeit, keine Zertifikatskette oder tatsächliche TLS-Erreichbarkeit.

Mail-/SSO-, DSM-HTTPS- und benötigte DC-Zertifikate ausdrücklich aufnehmen. Nach Rotation Entra,
DSM beziehungsweise DC-Verbindung prüfen und Inventardatei, Fingerabdruck sowie Secret-Referenz
gemeinsam aktualisieren.

Für CG gilt KI-13/ADR 0005: tatsächliches `accept_weak_dc_keys_until` und ersten
DC-Zertifikatsablauf prüfen; **spätestens vor 1. Januar 2027** neu entscheiden, bei früherem
konfiguriertem Enddatum entsprechend früher. Die Ausnahme bleibt befristet und mandantenbezogen;
Ketten- und Hostnamenprüfung bleiben aktiv.

## Update und Rückfall

1. Git-Commit, Image-ID, Plattform und private Konfigurationsversion im geschützten
   Betriebsnachweis erfassen. Wiederherstellungszugänge bereithalten.
2. `python -m app.cli backup` im Container ausführen und die Datei mit `backup-verify` prüfen. Bei
   Fehlern das Update abbrechen.
3. Freigegebenen Stand holen und Image bauen. Migrationen laufen beim App-Start; davor versucht
   der Start zusätzlich eine Sicherung.
4. Container neu erstellen. `/health`, Logs, Übersicht, Anmeldung, Kontenansicht, CSRF-Formular,
   Quellenlauf und Sicherungsstatus prüfen. Aktivierte Integrationen gezielt abnehmen.
5. Bei Problemen App stoppen. Ein älteres Image nicht mit einem neueren Schema starten. Passendes
   Image, private Konfiguration und Sicherung über den dokumentierten Restore-Befehl
   wiederherstellen. Restore verwirft alte Sitzungen.

```bash
docker compose exec app python -m app.cli backup
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health
docker compose logs --tail=100 app
```

## Störungen eingrenzen

| Symptom | Prüfung | Maßnahme |
|---|---|---|
| Container startet nicht | `docker compose logs app`; Konfigurationsfehler | Vorlage und [Konfigurationsreferenz](configuration.md) abgleichen; keine Migration überspringen |
| `/health` fehlschlägt | Containerstatus, Datenvolume, Schreibrechte | App stoppen; Datenbank und Volume sichern; Ursache klären |
| lokale Anmeldung gesperrt | `auth.login_failed`/`auth.lockout` im Audit | `user-list`, dann berechtigt `user-reset <name>` |
| SSO schlägt fehl | Origin, Redirect, Gruppe, Uhrzeit, SSO-Audit | [SSO-Prüfschritte](sso-setup.md) ausführen; lokalen Adminzugang erhalten |
| Quellenlauf fehlschlägt | **Läufe**, Warncode, `check-entra`/`check-ad` | Zertifikat, Secret, Berechtigung und Erreichbarkeit prüfen |
| Sicherung fehlschlägt | freier Platz, Rechte, `backup-list` | keine Updates; Ursache beheben und neue Sicherung verifizieren |
| externer Lauf fehlschlägt | Zielstatus, `external-backup-check` | lokales Backup erhalten; Zielzugang reparieren; nicht bereinigen |
| Bericht fehlschlägt | Berichtsseite, `mail-check`, Exchange-Zuweisung | Vorschau prüfen; Berechtigung korrigieren; bewusst neu senden |
| Zertifikatswarnung | Inventarpfad, Ablauf, tatsächlicher Dienst | rotieren, Dienst testen, Inventar/Fingerabdruck aktualisieren |

Audit-Ereignisse enthalten bewusst keine fachlichen Namen oder Secrets. Der vollständige Katalog
steht in [Audit-Log](audit-log.md).

## Sicherung und Wiederherstellung

Lokale Backups verwenden die SQLite-Online-Backup-API, Modus 0600 und eine zeitbasierte
Aufbewahrung. Sie liegen standardmäßig auf demselben Gerät und schützen daher nicht vor dessen
Ausfall. Das [externe Backup](external-backup.md) ist vorbereitet, bleibt ohne zweites Ziel jedoch
deaktiviert. Eine Restore-Probe erfolgt immer in ein neues Verzeichnis beziehungsweise bei lokalem
Restore mit gestoppter App und ausdrücklicher `--yes`-Bestätigung.

RPO (maximal tolerierter Datenverlust), RTO (Wiederanlaufzeit), Ziel, Aufbewahrung und unabhängiger
Notfallzugang müssen vor Aktivierung eines externen Ziels festgelegt werden.

## CI und Sicherheitswartung

CI prüft Format, Lint, Typen, Tests (mindestens 95 %), `pip-audit`, Container-Neustart,
Nicht-root-Betrieb und schreibgeschütztes Dateisystem. Ein echtes Restic-Binary prüft
Verschlüsselung, Integrität und Rückholung. Trivy erzeugt eine CycloneDX-SBOM; behebbare hohe oder
kritische Befunde blockieren. Berichte bleiben 14 Tage als CI-Artefakte.

Python- und Go-Abhängigkeiten sowie Compiler getrennt warten. Restic entsteht aus geprüftem
Upstream-Code; Details stehen in der [Build-Provenienz](../docker/restic/README.md). Nach Updates
alle Lockfiles mit Hashes konsistent erzeugen. Dependabot-PRs einzeln prüfen und nur mit grüner CI
sowie konsistenten transitiven Abhängigkeiten zusammenführen.

## Betriebsnachweise

- [x] DSM-HTTPS und Entra-SSO laut Niklas am 11. November 2026 erledigt.
- [ ] Zertifikatsinventar mit realen öffentlichen Dateien gepflegt; CG-Frist geprüft.
- [ ] Unabhängiges verschlüsseltes Sicherungsziel und Notfallzugang eingerichtet.
- [ ] Externer Restore auf getrenntem System mit gemessener RPO/RTO abgenommen.

Technische Entwicklung und lokale Tests schließen diese betrieblichen Nachweise nicht ab.
