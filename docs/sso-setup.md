# Entra-SSO mit der vorhandenen Mail-App

Phase 5.2. Nach Vorgabe von Niklas wird dieselbe App-Registrierung wie für den Mailversand
verwendet. Alle IDs und Fingerabdrücke in Vorlagen sind Platzhalter; echte Werte werden erst
beim Deployment eingetragen. SSO ist standardmäßig deaktiviert. HTTPS nach `https-setup.md`
zuerst einrichten und den lokalen Admin-Notzugang mit geändertem Passwort prüfen.

## 1. App im Softchem-Tenant ergänzen

1. Vorhandene App-Registrierung öffnen, vorhandene Mail-Einstellungen beibehalten.
2. Plattform **Web** ergänzen, Redirect-URI exakt:
   `https://accounts.softchem.netz/auth/entra/callback`.
   Ein einzelner Tenant; keine allgemeinen `common`-/Mehrmandanten-Endpunkte.
3. Microsoft Graph → **delegierte** Berechtigung `User.Read` hinzufügen und die erforderliche
   Zustimmung im eigenen Tenant erteilen. MSAL fordert zusätzlich die OIDC-Scopes an.
   Es werden keine zusätzlichen Graph-Anwendungsberechtigungen für SSO benötigt.
4. Bereits hinterlegtes Zertifikat wiederverwenden; privater Schlüssel bleibt im vorhandenen
   Docker-Secret. Die SSO-Einstellungen referenzieren denselben Secret-Namen/Fingerabdruck.
5. Sicherheitsgruppe für zugelassene Mitarbeitende der Softchem-IT festlegen. Optional eine
   zweite Sicherheitsgruppe für Tool-Admins. Mitgliedschaft in der Admin-Gruppe ersetzt nicht
   die notwendige Mitgliedschaft in der Zugangsgruppe. Die App fragt transitive Mitgliedschaften
   ab; Gruppen-Claims und deren Größenbegrenzung werden nicht zur Zulassung verwendet.
6. MFA/Conditional Access in Entra auf diese Unternehmensanwendung anwenden. Ein erfolgreicher
   OIDC-Login allein belegt keine MFA-Anforderung; das Tool erzwingt keine eigene MFA-Policy.

Die Exchange-RBAC-Begrenzung des Mailversands bleibt bestehen. Keine unbegrenzte
**Anwendungsberechtigung** `Mail.Send` ergänzen. Delegiertes `User.Read` und der bestehende
Anwendungs-Mailversand sind verschiedene Token-Flows, obwohl Client-ID/Zertifikat gleich sind.
Beide Funktionen hängen damit von derselben App und Zertifikatsrotation ab.

## 2. Deploymentwerte eintragen

Lokale `.env`, Platzhalter ersetzen; keine privaten Schlüssel oder Tokens eintragen:

```dotenv
KW_PUBLIC_URL=https://accounts.softchem.netz
KW_COOKIE_SECURE=1
KW_SSO_ENABLED=1
KW_SSO_TENANT_ID=<tenant-id>
KW_SSO_CLIENT_ID=<client-id-der-mail-app>
KW_SSO_ALLOWED_GROUP_ID=<zugangsgruppen-id>
KW_SSO_ADMIN_GROUP_ID=<optionale-admin-gruppen-id>
KW_SSO_CERT_SECRET=<name-des-vorhandenen-zertifikat-secrets>
KW_SSO_CERT_THUMBPRINT=<sha1-fingerabdruck>
KW_SSO_RECHECK_SECONDS=300
```

Ohne Admin-Gruppe den Eintrag leer lassen. In der lokalen Compose-Override-Datei muss das
referenzierte Zertifikat bereits als Secret am App-Dienst eingehängt sein (siehe Mail-Anleitung).
Für die Vertrauensliste des DSM-Proxys gilt weiterhin `https-setup.md`.

```bash
docker compose up -d --build
```

Eine fehlende/ungültige aktivierte SSO-Konfiguration bricht den Start ab. Nicht erreichbares
Entra oder fehlende Zertifikatsdateien verhindern die SSO-Anmeldung; der lokale Notzugang bleibt
verfügbar. Die lokale Anmeldung wird nicht automatisch abgeschaltet.

## 3. Verhalten und Sicherheitsgrenzen

- Anmeldeseite bietet „Mit Microsoft Entra anmelden“ und den lokalen Notzugang.
- Authorization Code Flow mit PKCE, State, Nonce und zusätzlicher eigener RS256-Signaturprüfung:
  Issuer, Audience, Gültigkeit, Tenant und Nonce werden kontrolliert. Keine impliziten Tokens.
- Callback ausschließlich per `form_post`. Das separate, kurzlebige Browsercookie
  `kw_sso_flow` hat `Secure`, `HttpOnly`, `SameSite=None` und ist zehn Minuten gültig. Der
  Flow ist serverseitig und einmalig. Das normale Sitzungscookie bleibt `SameSite=Lax`.
- Zulassung über aktuelles `GET /me/transitiveMemberOf` mit delegiertem `User.Read`, einschließlich
  Paging. Ein Fehler oder eine unvollständige Abfrage erlaubt keinen Zugang. Keine folgenden
  Seiten auf fremden Hosts. Versteckte Mitgliedschaften können besondere Rechte benötigen;
  die Zugangsgruppen im eigenen Tenant gezielt prüfen, statt pauschal Rechte zu erweitern.
- Revalidierung nach standardmäßig fünf Minuten, konfigurierbar bis höchstens 15 Minuten,
  spätestens bei der nächsten Anfrage danach. Gruppenentzug wirkt, sobald Graph die Änderung
  liefert. Bei Ausfall/entzogener Zustimmung endet die SSO-Sitzung; lokale Sitzungen bleiben nutzbar.
- SSO-Benutzer werden nur über Tenant-/Objekt-ID zugeordnet. Keine automatische Verknüpfung mit
  lokalen Namen oder E-Mail-Adressen. Die Oberfläche nennt sie `Entra-<Objekt-ID>`.
- Externe Benutzer besitzen kein lokales Passwort; Passwortreset und manuelle Admin-Vergabe
  im Tool sind für sie gesperrt. Deaktivierung im Tool bleibt möglich. Mindestens ein aktiver
  lokaler Admin bleibt erhalten. SSO-Adminrechte werden aus der konfigurierten Gruppe aktualisiert.
- Access-/Refresh-Tokens bleiben ausschließlich im Prozessspeicher. Ein Neustart oder Abschalten
  von SSO verlangt eine neue Entra-Anmeldung; Datenbank/Backups enthalten keine Entra-Tokens.
  Diese Implementierung ist für den bestehenden einzelnen App-Prozess ausgelegt.
- Abmelden beendet die Tool-Sitzung und verwirft den Cache. Die Entra-Browsersitzung wird nicht
  global beendet; erneute Anmeldung kann deren bestehende Sitzung verwenden.

## 4. Abnahme und Rückfall

1. Zugelassenes Gruppenmitglied kann anmelden und Konten öffnen; ein Nichtmitglied nicht.
2. Normaler SSO-Benutzer erreicht keine Admin-Seiten; Mitglied der zusätzlichen Admin-Gruppe schon.
3. Zugangsgruppe entziehen, Auswirkung in Graph abwarten, nach der Prüfzeit eine geschützte Seite
   aufrufen: erneute Anmeldung erforderlich. Nur Admin-Gruppe entziehen: Admin-Seite wird 403,
   normale Seiten bleiben erreichbar.
4. Callback ohne Browserbindung, mit falschem State oder Wiederholung wird abgelehnt. Keine
   fremden Weiterleitungsziele; keine Codes/Tokens im Log oder Audit-Log.
5. Neustart, Logout und ein blockierter Graph-Zugang prüfen; lokalen Admin-Notzugang testen.
6. `mail-check` ausführen und den unveränderten Mailbericht bei Bedarf kontrollieren.

Für den Rückfall `KW_SSO_ENABLED=0` setzen und den App-Container neu erstellen. Lokale Benutzer,
Ausnahmen und sonstige Mandantendaten bleiben erhalten. Datenbankmigration `0009` ergänzt nur
SSO-Referenzen und Sitzungstyp; vor der Migration greift die bestehende Sicherung.

## Quellen

- [MSAL Python: Authorization Code Flow](https://msal-python.readthedocs.io/en/latest/)
- [Microsoft Graph: transitive Mitgliedschaften](https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof?view=graph-rest-1.0)
- [Microsoft: ID-Token-Validierung](https://learn.microsoft.com/en-us/entra/identity-platform/id-tokens#validate-tokens)
