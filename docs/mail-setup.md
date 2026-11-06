# Mail-Bericht einrichten (Exchange Online, Microsoft Graph)

Der Wochenbericht wird über einen **zusätzlichen Softchem-Tenant** gesendet (ADR 0008). Dafür braucht
das Tool eine **eigene App-Registrierung** in diesem Tenant, getrennt von den lesenden Zugängen zu den
Kunden-Tenants (`docs/entra-setup.md`), und ein **Absenderpostfach** in Exchange Online.

Die Schritte laufen im Softchem-Tenant. Befehle für Exchange: PowerShell mit
`Connect-ExchangeOnline`. Die Cmdlets stammen aus Microsofts Doku „Role Based Access Control for
Applications in Exchange Online“; prüfe die Parameter dort, falls sich etwas geändert hat.

## 1. App-Registrierung

1. Entra Admin Center, Anwendungen, App-Registrierungen, **Neue Registrierung** (z. B. „Kontenwächter
   Mail“, nur dieser Mandant). Notiere **Anwendungs-ID** (`KW_MAIL_CLIENT_ID`) und
   **Verzeichnis-ID** (`KW_MAIL_TENANT_ID`).
2. **Zertifikate und Geheimnisse**: ein Zertifikat hochladen (öffentlicher Teil). Den
   **SHA-1-Fingerabdruck** notieren (`KW_MAIL_CERT_THUMBPRINT`). Den privaten Schlüssel (PEM) legst du
   als Docker-Secret ab, z. B. `mail_cert`; Zertifikat erzeugen, Fingerabdruck auslesen und Secret
   einhängen wie in `docs/entra-setup.md` (`KW_MAIL_CERT_SECRET=mail_cert`).
3. **Keine** Graph-Berechtigung `Mail.Send` in Entra erteilen. Sie würde für alle Postfächer des
   Tenants gelten und die Begrenzung in Schritt 3 aufheben (Entra und Exchange addieren sich).

## 2. Absenderpostfach

Ein Postfach in Exchange Online, aus dem gesendet wird, z. B. `kontenwaechter@<domäne>`. Es ist der
Wert von `KW_MAIL_FROM`. Ob ein freigegebenes Postfach ohne Lizenz genügt, ist nicht geprüft; ist das
Postfach für Graph nicht nutzbar, antwortet Graph mit 404 (Code `sender`).

## 3. Exchange-Berechtigung auf dieses Postfach begrenzen (RBAC for Applications)

Die Object-ID des Dienstprinzipals findest du unter **Unternehmensanwendungen** (nicht auf der Seite der
App-Registrierung; dort stehen andere Werte).

```powershell
Connect-ExchangeOnline
New-ServicePrincipal -AppId <Anwendungs-ID> -ObjectId <Object-ID des Dienstprinzipals> -DisplayName "Kontenwächter Mail"
New-ManagementScope -Name "Kontenwaechter Absender" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'kontenwaechter@<domäne>'"
New-ManagementRoleAssignment -App <Object-ID des Dienstprinzipals> -Role "Application Mail.Send" -CustomResourceScope "Kontenwaechter Absender"
```

Prüfen (umgeht den Zwischenspeicher):

```powershell
Test-ServicePrincipalAuthorization -Identity <Object-ID> -Resource kontenwaechter@<domäne>   # InScope: True
Test-ServicePrincipalAuthorization -Identity <Object-ID> -Resource <anderes Postfach>        # InScope: False
```

Änderungen wirken nach Microsoft nach 30 Minuten bis 2 Stunden (Zwischenspeicher). Wer bisher
Application Access Policies nutzt, findet die Migration in derselben Doku.

## 4. Tool einrichten

In `.env` neben `docker-compose.yml` (Vorlage `.env.example`):

```
KW_MAIL_FROM=kontenwaechter@<domäne>
KW_MAIL_TENANT_ID=<Verzeichnis-ID>
KW_MAIL_CLIENT_ID=<Anwendungs-ID>
KW_MAIL_CERT_SECRET=mail_cert
KW_MAIL_CERT_THUMBPRINT=<SHA-1-Fingerabdruck>
```

Empfänger je Mandant in `config/tenants.yaml`:

```yaml
    report:
      recipients: ["it@example.com"]
```

Container neu starten, dann:

```bash
docker compose exec app python -m app.cli mail-check          # Anmeldung der Mail-App (Token)
docker compose exec app python -m app.cli report-preview cg   # Bericht ansehen
docker compose exec app python -m app.cli report-send cg      # echter Versand
```

## Fehlercodes

| Code | Bedeutung | Prüfen |
|---|---|---|
| `secret` | Zertifikat-Secret fehlt oder ist nicht lesbar | Secret eingehängt? Name in `KW_MAIL_CERT_SECRET` |
| `auth` | Token nicht erhältlich oder 401 | Zertifikat im Entra hochgeladen? Fingerabdruck, Verzeichnis-ID und Anwendungs-ID |
| `forbidden` | 403, Exchange verweigert | Schritt 3 (Rolle, Bereich, Wartezeit); in Entra keine zusätzliche Zustimmung nötig |
| `sender` | 404, Postfach unbekannt oder nicht für Graph aktiviert | `KW_MAIL_FROM` richtig? Postfach in Exchange Online vorhanden? |
| `rejected` | 400, Exchange lehnt Inhalt oder Empfänger ab | Empfängeradressen in `tenants.yaml` |
| `throttled` | 429, Exchange drosselt | später erneut |
| `connect` | Graph oder Entra nicht erreichbar | Netzwerk, Proxy |
| `send` | sonstige Antwort | Log (technischer Graph-Fehlercode) |

Der technische Graph-Code (z. B. `ErrorAccessDenied`) steht im Log und im Audit-Log
(`report.failed`, Feld `graph=`), nie Adressen oder Meldungstexte.
