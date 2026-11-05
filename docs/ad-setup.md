# AD-Anbindung einrichten (je Mandant)

Gilt für jeden Mandanten einzeln (CG, CVH). Der Kontenwächter liest das On-Prem-AD direkt per
LDAPS (Port 636) mit einem eigenen Dienstkonto je Domäne. Hintergrund:
[ADR 0002](adr/0002-ad-direkt-per-ldaps.md), Ablauf: [plan.md](plan.md), Abschnitt „AD-Anbindung“.

> **Stand:** Der AD-Connector ist noch nicht umgesetzt (Phase 3). Diese Anleitung bereitet alles
> vor, was ohne ihn möglich ist: Konto, Secret, Konfiguration, Netzwerk und Zertifikate. Es gibt
> noch keinen Bind- oder DC-Test (`check-ad`). Ob die Standardrechte für alle Attribute reichen,
> zeigt erst der Abnahmelauf in Phase 3.

## 1. Dienstkonto in der Domäne

Ein eigenes Konto pro Domäne, zum Beispiel `svc-kontenwaechter`:

- Mitglied **nur** in „Domain Users“. Keine Admin- oder Operator-Gruppen, keine delegierten Rechte.
- Nur lesend. Der Kontenwächter ändert, deaktiviert oder löscht nie Konten.
- Passwort lang und zufällig, nur im Docker-Secret abgelegt (Schritt 2).
- Passwortablauf nach der Richtlinie des Kunden entscheiden. Läuft das Passwort ab, scheitert der
  Bind, bis ein neues Secret eingespielt ist.
- Interaktive Anmeldung per GPO verbieten: „Lokal anmelden verweigern“, „Anmeldung über
  Remotedesktopdienste verweigern“ und „Anmelden als Stapelverarbeitungsauftrag verweigern“
  für das Konto.

Der Name des Kontos passt zum Muster `svc*` in `account_patterns` (Kontoart „Dienst“), damit es
später nicht selbst als inaktives Konto auffällt.

## 2. Passwort als Docker-Secret ablegen

Auf dem Docker-Host, nicht im Repo. `printf` statt `echo`, damit kein Zeilenumbruch in die Datei
gelangt (der Code entfernt abschließende Zeilenumbrüche, aber so bleibt die Datei sauber):

```bash
umask 077
printf '%s' 'PASSWORT_HIER' > /sicherer/pfad/cg_ad_bind
```

Das Passwort steht sonst in der Shell-History: mit führendem Leerzeichen eingeben oder mit
`read -rs` einlesen. Dasselbe für `cvh_ad_bind`.

Der Container läuft als UID 10001. Die Datei gehört diesem Benutzer und ist nur für ihn lesbar:

```bash
sudo chown 10001:10001 /sicherer/pfad/cg_ad_bind
```

```bash
sudo chmod 0400 /sicherer/pfad/cg_ad_bind
```

Jede Datei, die für andere lesbar ist (zum Beispiel `0755`), macht das Passwort für alle lokalen
Benutzer des Hosts lesbar.

## 3. Secret einhängen

In der lokalen `docker-compose.override.yml` (steht in `.gitignore`) neben den Entra-Secrets:

```yaml
services:
  app:
    secrets: [cg_entra_cert, cg_ad_bind, cvh_entra_cert, cvh_ad_bind]
secrets:
  cg_ad_bind:
    file: /sicherer/pfad/cg_ad_bind
  cvh_ad_bind:
    file: /sicherer/pfad/cvh_ad_bind
```

## 4. Konfiguration

In `config/tenants.yaml` beim jeweiligen Mandanten:

```yaml
ad:
  domain: cg.example.invalid     # Domänenname
  dcs: []                        # leer = DCs per DNS-SRV finden; sonst feste Liste von Hostnamen
  bind_user: svc-kontenwaechter  # Name des Dienstkontos
  bind_secret: cg_ad_bind        # nur der NAME des Secrets, nie das Passwort
```

- Unbekannte Schlüssel brechen den Start ab.
- Findet der Container die DCs nicht per DNS-SRV (`_ldap._tcp.dc._msdcs.<domäne>`), trage sie
  fest unter `dcs:` ein. Das ist bei getrennten Kundennetzen oft nötig.
- Das Format von `bind_user` (sAMAccountName, UPN oder DN) legt Phase 3 fest. Bis dahin die
  Schreibweise der Vorlage beibehalten.

Neu starten und prüfen:

```bash
docker compose up -d
```

```bash
docker compose exec app python -m app.cli tenants
```

```bash
docker compose exec app ls -l /run/secrets
```

Alle Secrets sollten `-r-------- 1 kw kw` zeigen.

## 5. Netzwerk und Zertifikate

- Der Docker-Host muss **alle** DCs der Domäne auf Port 636 erreichen. Wichtig, weil `lastLogon`
  nicht repliziert wird und von jedem DC einzeln gelesen werden muss. Ein nicht erreichbarer DC
  kann dazu führen, dass die letzte Anmeldung zu alt erscheint.
- Jeder DC braucht ein gültiges LDAPS-Zertifikat, dessen Name zum DC-Namen passt.
- Die CA, die diese Zertifikate ausgestellt hat, muss im Container vertraut werden. Es gibt kein
  `verify=False` und kein LDAP ohne TLS. Das Einhängen des CA-Zertifikats kommt mit Phase 3.

Erreichbarkeit und Zertifikat lassen sich vom Host aus prüfen (`ca.pem` ist die Aussteller-CA):

```bash
openssl s_client -connect dc1.cg.example.invalid:636 -CAfile ca.pem </dev/null 2>/dev/null | grep -E "Verify return code|subject="
```

Erwartet wird `Verify return code: 0 (ok)`.

## 6. Berechtigungen im Überblick

Das Konto liest mit den Standardrechten von „Authenticated Users“:

- Stammdaten: `objectGUID`, `sAMAccountName`, `userPrincipalName`, `mail`, `userAccountControl`,
  `whenCreated`, `pwdLastSet`, `accountExpires`, `description`, `distinguishedName`, `memberOf`
- Anmeldung: `lastLogonTimestamp` und `lastLogon` (je DC)
- Gesundheitscheck: `userAccountControl` und `whenCreated` von Dienstkonto, Administrator, `krbtgt`

## Fehlerbilder

| Ausgabe | Bedeutung |
|---|---|
| `Secret 'x' nicht lesbar` | Secret nicht eingehängt, falscher Name oder Datei nicht für UID 10001 lesbar |
| Start bricht mit Feldpfad ab | Tippfehler oder unbekannter Schlüssel in `tenants.yaml` |
| Zertifikatsfehler beim Bind (ab Phase 3) | CA nicht vertraut oder Name im Zertifikat passt nicht zum DC |
| Bind abgelehnt (ab Phase 3) | Passwort, Kontoformat oder Kontosperre prüfen |
