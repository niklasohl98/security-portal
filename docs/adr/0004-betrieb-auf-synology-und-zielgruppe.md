# 0004 – Betrieb auf der Synology, Zielgruppe nur Softchem-IT

- **Status:** Accepted (2026-11-02); Proxy-Wahl und Details in Phase 5 offen

## Kontext
Der Container läuft auf einer Synology-NAS (Container Manager). Nur die Softchem-IT nutzt die
Oberfläche. Der Docker-Host erreicht alle DCs beider Kunden auf Port 636.

## Entscheidung
- Betrieb als Docker-Compose-Projekt auf der Synology; das Image bleibt unverändert
  (Nicht-root, schreibgeschütztes Dateisystem, `/data` als Volume).
- Kein Rollenmodell für Kunden. Zugriff über eine Sicherheitsgruppe im Softchem-Entra-Mandanten.
  Die Trennung per `tenant_id` bleibt in jeder Abfrage Pflicht.
- Backups zunächst lokal auf der Synology (befristet akzeptiert, KI-6).

## Alternativen
- **Caddy im Compose** (Plan) oder **Reverse-Proxy von DSM:** noch offen. DSM bringt HTTPS und
  Zertifikatsverwaltung mit, braucht keinen zweiten Container, bindet das Tool aber an die
  DSM-Konfiguration. Entscheidung in Phase 5 nach Test am Gerät.
- **Windows-Agent bei CVH:** nicht nötig, da Port 636 erreichbar ist (ADR 0002).

## Konsequenzen
- In Phase 5 am echten Gerät prüfen: DSM-/Container-Manager-Version, CPU-Architektur (das Image
  ist per Digest auf die Multi-Arch-Liste gepinnt), Docker-Secrets aus Dateien, `read_only`,
  `cap_drop`, `no-new-privileges`, Schreibrechte des Volumes für UID 10001.
- SQLite liegt auf dem lokalen Synology-Volume, nicht auf einer Netzfreigabe (WAL-Modus und
  Netzdateisysteme vertragen sich nicht).
