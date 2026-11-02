# 0003 – Zertifikats-Login für Graph und Docker-Secrets

- **Status:** Accepted (2026-11-02)

## Kontext
Das Tool braucht dauerhaften, rein lesenden Zugriff auf Entra und AD und darf keine langlebigen
Klartext-Geheimnisse in Repo, Image oder Datenbank hinterlassen.

## Entscheidung
Je Mandant eigene App-Registrierung (Anwendungsberechtigungen, Client-Credentials) mit
Zertifikat statt Client-Secret. Private Schlüssel und AD-Passwörter als Docker-Secrets unter
`/run/secrets/`. Konfiguration und Datenbank speichern nur den Secret-Namen. Zertifikatsablauf wird
täglich geprüft (Warnung 30 Tage vorher).

## Alternativen
- **Client-Secret:** Ablauf, landet leicht in Konfigurationen.
- **Umgebungsvariablen / `.env`:** erscheinen in `docker inspect`, Prozesslisten und Backups.

## Konsequenzen
Zertifikate müssen alle 2 Jahre erneuert werden. Ein Docker-Secret ist je Compose-Datei zu
deklarieren; die Dateien liegen außerhalb des Repos.
