# AD-Anbindung einrichten (je Mandant)

Gilt für jede Domäne einzeln. Das Tool liest per LDAPS (Port 636) aus dem Container auf der
Synology und ändert **nie** etwas im AD. Hintergrund: [ADR 0002](adr/0002-ad-direkt-per-ldaps.md),
[ADR 0005](adr/0005-befristete-ausnahme-fuer-schwache-dc-schluessel.md).

Alle Namen unten (`example.invalid`, `svc-kontenwaechter`) sind Platzhalter.

## 1. Voraussetzungen

- Die Synology erreicht alle DCs auf Port 636 und löst die Domäne per DNS auf (SRV-Eintrag
  `_ldap._tcp.dc._msdcs.<domäne>`). Prüfen:

  ```bash
  nslookup -type=SRV _ldap._tcp.dc._msdcs.cg.example.invalid
  ```

- Ein Dienstkonto je Domäne, **nur Mitglied in „Domain Users“**, langes zufälliges Passwort,
  interaktive Anmeldung per GPO verbieten. Das Tool braucht nur Leserechte.
- Das Passwort liegt als Docker-Secret (siehe Schritt 4), nie im Repo.

## 2. LDAPS und Zertifikat prüfen

Von der Synology aus (ändert nichts). Die Ausgabe zeigt Aussteller, Schlüssellänge und
Signatur eines DC-Zertifikats:

```bash
echo | openssl s_client -connect dc1.cg.example.invalid:636 -servername dc1.cg.example.invalid 2>/dev/null \
  | openssl x509 -noout -text | grep -E "Issuer:|Public-Key|Signature Algorithm" | head -3
```

Im Container selbst, mit derselben Prüfung wie später in der App (nur die CA aus Schritt 3):

```bash
docker compose exec app python -c "import ssl,socket; h='dc1.cg.example.invalid'; c=ssl.create_default_context(cafile='/config/ca/cg-root-ca.pem'); s=c.wrap_socket(socket.create_connection((h,636),5),server_hostname=h); print(h, s.version(), s.getpeercert()['notAfter'])"
```

| Ausgabe | Bedeutung |
|---|---|
| Zeile mit TLS-Version und Datum | Container vertraut dem DC |
| `unable to get local issuer certificate` | CA fehlt im Container: Schritt 3 |
| `EE certificate key too weak` | DC-Zertifikat hat RSA unter 2048 Bit: neu ausstellen oder die befristete Ausnahme (Schritt 5) |
| `Hostname mismatch` | Der DC-Name steht nicht im Zertifikat; mit dem FQDN verbinden, nicht mit der IP |
| `certificate has expired` | DC-Zertifikat abgelaufen |

## 3. CA-Datei bereitstellen

Die CA der Domäne (Stamm- oder ausstellende CA) als PEM-Datei unter `config/ca/` ablegen. Die Datei
ist öffentlich, beschreibt aber die Infrastruktur eines Kunden; `config/ca/` steht deshalb in
`.gitignore` und `.dockerignore`.

Auf einem Windows-Rechner, dem die CA vertraut (PowerShell, exportiert als DER):

```powershell
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -match 'Name Ihrer CA' |
  Select-Object Subject, Issuer, NotAfter, Thumbprint
Export-Certificate -Cert Cert:\LocalMachine\Root\<Thumbprint> -FilePath cg-root-ca.cer
```

Auf der Synology nach PEM umwandeln und den Fingerabdruck mit dem Thumbprint vergleichen:

```bash
mkdir -p config/ca
sudo openssl x509 -inform DER -in cg-root-ca.cer -out config/ca/cg-root-ca.pem
sudo openssl x509 -in config/ca/cg-root-ca.pem -noout -subject -fingerprint -sha1
```

Der Ordner `config/` ist im Container unter `/config` eingehängt (nur lesend).

## 4. Dienstkonto-Passwort als Docker-Secret

Wie beim Entra-Zertifikat ([entra-setup.md](entra-setup.md), Schritt 4): Datei außerhalb des Repos,
Besitzer `10001`, Modus `0400`, und in der `docker-compose.override.yml` einhängen:

```yaml
services:
  app:
    secrets: [cg_ad_bind]
secrets:
  cg_ad_bind:
    file: /volume1/docker/kontenwaechter-secrets/cg_ad_bind
```

Die Datei enthält nur das Passwort (ein abschließender Zeilenumbruch wird entfernt). Danach den
Container neu erstellen: `docker compose up -d`.

## 5. Konfiguration

In `config/tenants.yaml` beim jeweiligen Mandanten:

```yaml
ad:
  domain: cg.example.invalid
  dcs: []                          # leer = DCs per DNS-SRV finden; sonst feste Liste von FQDNs
  bind_user: svc-kontenwaechter    # Kurzname; wird zu <name>@<domain> (mit @ oder \ unverändert)
  bind_secret: cg_ad_bind          # Name des Docker-Secrets
  ca_file: ca/cg-root-ca.pem       # relativ zu config/; ohne Angabe gilt der Zertifikatsspeicher
  # accept_weak_dc_keys_until: 2026-12-31   # nur wenn nötig, siehe unten
```

`dcs` immer mit **FQDN**: Das Zertifikat wird gegen den DC-Namen geprüft, eine IP passt nicht.

**Befristete Ausnahme für zu kurze DC-Schlüssel** (ADR 0005): `accept_weak_dc_keys_until` erlaubt bis
einschließlich diesem Datum RSA-Schlüssel ab 1024 Bit für die Verbindungen dieses einen
Mandanten. Kette, Hostname und Ablauf werden weiter geprüft. Jeder Lauf trägt dann die Warnung
`ad_weak_key_exception_active`; nach dem Datum gilt wieder Standard (Warnung
`ad_weak_key_exception_expired`) und die Verbindung scheitert, solange der Schlüssel zu kurz ist.

## 6. Testen

Trockenlauf: findet die DCs, prüft jeden, liest die Konten und speichert **nichts**:

```bash
docker compose exec app python -m app.cli check-ad cg
```

Abruf ausführen und speichern:

```bash
docker compose exec app python -m app.cli sync-ad cg
```

Die Ausgabe enthält nur Zähler, DC-Namen und Status, keine Kontodaten.

### Was der Lauf prüft

1. DCs finden (feste Liste oder DNS-SRV).
2. Je DC: LDAPS-Verbindung mit Zertifikatsprüfung, Bind, dann **Gesundheitscheck**: Dienstkonto,
   `krbtgt` und `Administrator` müssen gefüllte `userAccountControl` und `whenCreated` liefern.
   `Administrator` darf fehlen (umbenannt). Sonst gilt der DC als **defekt** und liefert keine
   Stammdaten.
3. Stammdaten vom ersten gesunden DC; Konten mit leeren Stammdaten werden von einem zweiten
   gesunden DC ergänzt und mit `master_data_incomplete` markiert.
4. `lastLogon` von jedem erreichbaren DC (auch von defekten); je Konto zählt der jüngste Wert, die
   Einzelwerte stehen in `last_logon_per_dc`. `lastLogonTimestamp` bleibt getrennt.
5. Objektanzahl je DC gegen den Median: weicht ein DC um mehr als 1 % ab, ist er **verdächtig**.
   Der Vergleich gilt erst ab 3 gesunden DCs.

| DC-Status | Bedeutung |
|---|---|
| `ok` | gesund |
| `unreachable` | Verbindung, TLS, Bind oder Suche fehlgeschlagen (Grund: `tls`, `connect`, `bind`, `ldap`) |
| `defective` | antwortet, aber Referenzobjekte leer oder fehlend (`reference_empty`, `reference_missing`) |
| `suspect` | Objektanzahl weicht ab (`count_deviates`) |

Der Lauf ist `success`, wenn alles in Ordnung ist, `partial` bei Warnungen zu DCs oder
unvollständigen Stammdaten (dann kann `lastLogon` zu alt sein), und `failed`, wenn kein DC
erreichbar und gesund war (`AdError code=no_healthy_dc`).

### Fehlerbilder

| Ausgabe | Bedeutung |
|---|---|
| `AdError code=no_dc_found` | DNS-SRV liefert nichts: DNS der Synology prüfen oder `dcs` eintragen |
| `AdError code=tls` | Zertifikatsprüfung scheitert: Schritt 2 (CA, Schlüssellänge, Hostname, Ablauf) |
| `AdError code=connect` | Port 636 nicht erreichbar oder Zeitüberschreitung |
| `AdError code=bind` | Dienstkonto oder Passwort falsch bzw. gesperrt |
| `AdError code=no_healthy_dc` | Kein DC war erreichbar und gesund; die Einzelgründe stehen im Lauf |
| `Secret 'x' nicht lesbar` | Secret nicht eingehängt, falscher Name oder falsche Rechte |
| `CA-Datei nicht lesbar …` | `ca_file` fehlt, ist keine Datei oder liegt außerhalb von `config/` |

Bekannt: ein DC mit dauerhaft leeren Attributen (KI-8) erscheint bei jedem Lauf als `defective`,
der Lauf ist dann `partial`.
