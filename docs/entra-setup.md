# Entra-Anbindung einrichten (je Mandant)

Gilt für jeden Mandanten einzeln (CG, CVH). Der Kontenwächter braucht eine **eigene
App-Registrierung pro Mandant** mit reinen Lese-Anwendungsberechtigungen und Zertifikats-Login.
Hintergrund: [ADR 0003](adr/0003-zertifikats-login-und-docker-secrets.md).

## 1. Zertifikat erzeugen (auf einem sicheren Rechner, nicht im Repo)

```bash
openssl req -x509 -newkey rsa:2048 -sha256 -days 730 -nodes \
  -keyout cg_entra_cert.pem -out cg_entra_cert.crt -subj "/CN=Kontenwaechter-CG"
```

Fingerabdruck (SHA-1, 40 Hex-Zeichen, so erwartet ihn `tenants.yaml`):

```bash
openssl x509 -in cg_entra_cert.crt -noout -fingerprint -sha1 | sed 's/.*=//; s/://g'
```

Unter Windows/PowerShell gilt statt `\` der Backtick `` ` ``, und `sed` gibt es nicht. Immer
`-keyout` und `-out` angeben, sonst landet das Zertifikat nur in der Konsole und der Schlüssel als
`privkey.pem` im aktuellen Ordner:

```powershell
openssl req -x509 -newkey rsa:2048 -sha256 -days 730 -nodes `
  -keyout cg_entra_cert.pem -out cg_entra_cert.crt -subj "/CN=Kontenwaechter-CG"
(openssl x509 -in cg_entra_cert.crt -noout -fingerprint -sha1) -replace '.*=','' -replace ':',''
```

- `cg_entra_cert.crt` (öffentlich) wird in Entra hochgeladen.
- `cg_entra_cert.pem` (**privater Schlüssel**, unverschlüsselt, `BEGIN PRIVATE KEY`) wird das
  Docker-Secret. Nicht ins Repo, nicht per Mail, Zugriffsrechte nur für den Betreiber.
- Ablauf in 730 Tagen vormerken; ab Phase 4 warnt die Übersicht 30 Tage vorher.

## 2. App-Registrierung im Entra Admin Center

1. Entra Admin Center → App-Registrierungen → Neue Registrierung → Name „Inaktive-Konten-Wächter“,
   nur dieser Mandant (Single Tenant). Kein Redirect-URI.
2. Zertifikate und Geheimnisse → Zertifikate → `cg_entra_cert.crt` hochladen. Der angezeigte
   Fingerabdruck muss mit dem aus Schritt 1 übereinstimmen.
3. API-Berechtigungen → Microsoft Graph → **Anwendungsberechtigungen**:
   - `User.Read.All`
   - `AuditLog.Read.All` (für `signInActivity`)
   - `Organization.Read.All`
4. Administratorzustimmung erteilen.
5. Übersicht: **Verzeichnis-ID (Tenant-ID)** und **Anwendungs-ID (Client-ID)** notieren.

`Group.Read.All` ist derzeit **nicht** nötig: gruppenbasierte Lizenzen erkennt der Abruf über
`licenseAssignmentStates`. Für `GET /subscribedSkus` wäre `LicenseAssignment.Read.All` die
Berechtigung mit den geringsten Rechten (Graph-Doku); `Organization.Read.All` genügt ebenfalls
und wird für den Mandantennamen gebraucht.

`signInActivity` setzt Entra ID P1 oder P2 im Mandanten voraus. Fehlt sie, läuft der Abruf
ohne Anmeldedaten weiter (Lauf-Status `partial`, Warnung `sign_in_activity_unavailable`).

## 3. Konfiguration

In `config/tenants.yaml` beim jeweiligen Mandanten eintragen:

```yaml
entra:
  tenant_id: <Verzeichnis-ID>
  client_id: <Anwendungs-ID>
  certificate_secret: cg_entra_cert          # Name des Docker-Secrets
  certificate_thumbprint: <40 Hex-Zeichen>
```

## 4. Secret einhängen

Eine lokale Datei `docker-compose.override.yml` (steht in `.gitignore`) anlegen:

```yaml
services:
  app:
    secrets: [cg_entra_cert]
secrets:
  cg_entra_cert:
    file: /sicherer/pfad/cg_entra_cert.pem
```

Bei mehreren Mandanten jeweils ein Secret ergänzen (`cvh_entra_cert`, …); es muss **sowohl** in der
Liste `secrets:` beim Dienst **als auch** im Block `secrets:` stehen.

Auf der Synology liegt die Schlüsseldatei außerhalb des Projekts, zum Beispiel unter
`/volume1/docker/kontenwaechter-secrets/`. Der Container läuft als Benutzer 10001, also:

```bash
sudo chown 10001:10001 /volume1/docker/kontenwaechter-secrets/cg_entra_cert
sudo chmod 0400 /volume1/docker/kontenwaechter-secrets/cg_entra_cert
```

Danach den Container neu erstellen (ein neues Secret wird nur dabei eingehängt, `restart` genügt
nicht) und warten, bis er `healthy` ist:

```bash
docker compose up -d
docker compose ps
```

Die Datenbanktabellen entstehen erst beim Start der App. Läuft ein CLI-Befehl zu früh oder ist der
Start an `config/tenants.yaml` gescheitert (`docker compose logs --tail 30 app`), meldet er
`no such table: tenant`.

## 5. Testen

Verbindung prüfen (Token, Organisation, SKU-Zahl, `signInActivity`); ändert nichts:

```bash
docker compose exec app python -m app.cli check-entra cg
```

Einen Abruf ausführen und speichern (Konten- und SKU-Zahl, Warnungen):

```bash
docker compose exec app python -m app.cli sync-entra cg
```

Die Ausgabe enthält nur Zähler und Status. Vergleiche **Konten** und **SKUs** mit dem Graph
Explorer (`GET /users/$count` mit Header `ConsistencyLevel: eventual`, `GET /subscribedSkus`).

## Fehlerbilder

| Ausgabe | Bedeutung |
|---|---|
| `Secret 'x' nicht lesbar` | Secret nicht in der Compose-Datei eingehängt (Liste und Block prüfen), Container nicht neu erstellt, falscher Name, oder die Datei gehört nicht `10001` / hat nicht `0400` |
| `no such table: tenant` | App noch nicht gestartet oder Start fehlgeschlagen: `docker compose logs --tail 30 app` |
| `code=auth:invalid_client` | Zertifikat/Fingerabdruck passt nicht zur App-Registrierung: Fingerabdruck in `tenants.yaml` gegen Entra prüfen, Schlüssel im Secret zum hochgeladenen `.crt`, `client_id` aus der richtigen Registrierung |
| `code=auth:...` allgemein | Mandanten-ID, Client-ID oder Zertifikat falsch |
| `status=403` beim Abruf | Administratorzustimmung oder Berechtigung fehlt |
| `signInActivity: NICHT verfügbar` | P1/P2 fehlt oder `AuditLog.Read.All` nicht erteilt |
