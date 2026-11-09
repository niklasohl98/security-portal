# 0012 – Optionale externe Sicherung mit Restic

- **Status:** Accepted (2026-11-09); deaktiviert bis Ziel und Zugang bereitstehen
- **Fortschreibung:** ADR 0013 ersetzt nach dem Image-Scan den offiziellen Binärbezug durch
  einen Sicherheitsbuild aus geprüften Upstream-Quellen; Sicherungsprotokoll und Schnittstellen bleiben.

## Kontext

Niklas hat noch kein zweites Sicherungsziel. Die Mechanismen sollen vollständig vorbereitet,
aber bis zur späteren Konfiguration deaktiviert sein. Lokale SQLite-Sicherungen sind vorhanden.

## Entscheidung

- Explizites Opt-in `KW_EXTERNAL_BACKUP_ENABLED=1`; vorher kein Job/Verbindungsversuch und keine
  Warnung wegen fehlendem Ziel. Bestehende lokale Sicherungen bleiben unabhängig bestehen.
- Restic für verschlüsselte Inhalte/Metadaten und Zielzugriff über HTTPS-REST, HTTPS-S3 oder SFTP.
  Offizielles Multi-Arch-Image wird per geprüftem Digest gepinnt; statisches Binary im App-Image,
  OpenSSH-Client für SFTP. Keine eigene Kryptografie-/Transportimplementierung.
- Ein Paket aus konsistenter geprüfter SQLite-Kopie, benötigter nicht-geheimer Konfiguration
  und Manifest. Private Schlüssel und Secret-Werte werden separat im Notfallzugang gesichert.
- Credentials nur über Docker-Secret-Referenzen, für Restic über Passwortdatei bzw. begrenzte
  Subprozessumgebung. Keine Shell, keine externen Ausgaben in Logs, TLS-/Hostkey-Prüfung aktiv.
- Täglicher eigener Job nach Opt-in. Separater CLI-Zugang für Initialisierung, vollständige
  Integritätsprüfung, explizit bestätigte Aufbewahrung und Rückholung in ein neues Verzeichnis.
  Keine automatische Initialisierung, Löschung oder Ersetzung der laufenden Datenbank.
- Zielbezogener Status mit Alter/Fehlern in der Oberfläche und Audit-Ereignissen; Fehler dürfen
  lokale Sicherung und Tagesabruf nicht beeinträchtigen.

## Alternativen und Konsequenzen

Synology Hyper Backup kann später als zusätzliches Betriebsmittel bewertet werden. Die App-
Funktion stellt ein überprüfbares, verschlüsseltes Paket unabhängig von einem konkreten NAS-
Produkt bereit. Ein eigenes Restic-Repository pro Instanz begrenzt Auswahl und Aufbewahrung.

Eine lokale verschlüsselte Testablage belegt den technischen Roundtrip, keine externe
Ausfallsicherheit. Zielwahl, separate Notfallzugänge, RPO/RTO und Live-Restore stehen noch aus.
RPO 24 Stunden/RTO 4 Stunden sind Vorschläge, keine zugesicherte Eigenschaft.
Einrichtung und Recovery: `docs/external-backup.md`. KI-6/KI-30 bleiben bis zur Aktivierung und
Abnahme des externen Ziels offen. Der aktuelle einzelne Prozess schützt Aktionen mit einer
prozesslokalen Sperre; mehrere Prozesse benötigen ein neues Betriebskonzept.
