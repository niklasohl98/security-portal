# 0013 – Betriebspflege, Zertifikatsinventar und geprüfter Restic-Build

- **Status:** Accepted (2026-11-09)
- Fortschreibung von ADR 0012 hinsichtlich des Restic-Binärbezugs

## Kontext

Phase 5.4 verlangt nachvollziehbare Updates/Recovery, Ablaufwarnungen und einen Image-Scan.
Der erste Scan meldet behebbare hohe Befunde im bisherigen Debian-Unterbau sowie in Go und
Bibliotheken des aktuellen offiziellen Restic-Images. Nur einen Image-Scan hinzuzufügen,
ohne diese Befunde zu beheben, würde den neuen CI-Job unmittelbar rot machen.

## Entscheidung

- Öffentliche Zertifikate über ein optionales, validiertes Inventar überwachen. Keine privaten
  Schlüssel einlesen; Fristen sind konfigurierbar. Ablauf-/Lesefehler in Übersicht und Läufen
  anzeigen. Prüfung der tatsächlichen TLS-Verbindung bleibt beim jeweiligen Connector/Proxy.
- Uvicorn über `app.server` starten: JSON-Logging vor der ersten Serverzeile, genau ein Prozess,
  keine Access-Logs und keine universelle Proxy-Vertrauensliste.
- Aktuellen Python-Basis-Digest verwenden und Debian-Sicherheitsaktualisierungen beim Build
  einspielen. Compiler/Actions per tatsächlichem Digest/Commit pinnen.
- Restic 0.19.1 aus dem unveränderten, checksumgeprüften Upstream-Modul mit Go 1.27.3 und
  aktualisierten, in `docker/restic/go.mod`/`go.sum` gesperrten Sicherheitsabhängigkeiten bauen.
  Go-Modulprüfung und kurze Upstream-Backendtests laufen im Build. Kein ungekennzeichnetes
  Gleichsetzen mit dem offiziellen Binärartefakt; Provenienz und Wartung dokumentieren.
- CycloneDX-SBOM/Schwachstellenbestand erzeugen und behebbare HIGH-/CRITICAL-Befunde blockieren.
  Keine pauschalen CVE-Ausnahmen. Ungefixte Befunde bleiben sichtbar zur Bewertung.
- Externe Sicherungsaktionen bei Timeout samt eventuellem SSH-Kindprozess beenden. Rückholung
  weiterhin nur in ein neues Verzeichnis; lokale Datenbank separat und bewusst wiederherstellen.
  Beim tatsächlichen Datenbank-Restore alte Sitzungen löschen, damit frühere Abmeldungen nicht
  durch einen älteren Sicherungsstand rückgängig werden.

## Konsequenzen

Der Go-Build erhöht die Buildzeit und erfordert eigene Pflege der gesperrten Abhängigkeiten.
Sobald ein offizielles stabiles Binärartefakt dieselben Sicherheits-/Funktionstests besteht,
kann ein Folge-PR den Bezug wieder vereinfachen. Inventardateien/öffentliche Zertifikate und
Live-Notfallzugänge müssen vom Betreiber gepflegt werden. CI-Nachweise ersetzen keine
Synology-/Entra-/externen Restore-Abnahme. Betriebsanleitung: `docs/operations.md`.
