# HTTPS auf der Synology

Phase 5.1, Variante DSM-Reverse-Proxy. Vorgesehene Adresse nach Vorgabe von Niklas:
`https://accounts.softchem.netz`. Niklas hat HTTPS am 11. November 2026 als erledigt bestätigt.
Die folgenden Schritte bleiben die Referenz für Prüfung, Wartung und Wiederaufbau; konkrete
Zertifikate und Netzwerte stehen nur in der geschützten Deploymentkonfiguration.

## 1. DNS und Zertifikat

- DNS für `accounts.softchem.netz` auf die vorgesehene Synology-/Proxy-Adresse auflösen lassen.
  Erreichbarkeit auf die vorgesehenen Netze begrenzen.
- In DSM ein gültiges Zertifikat für genau diesen Namen bereitstellen und dem Reverse-Proxy
  zuweisen. Die Zertifikatskette muss auf allen zugreifenden Clients vertrauenswürdig sein.
  Bei interner Namensauflösung/CA deren Vertrauenskette verteilen; öffentliche ACME-Ausstellung
  wird hier nicht vorausgesetzt. Private Schlüssel bleiben in der Zertifikatsverwaltung von DSM.

## 2. DSM-Reverse-Proxy

DSM → Anmeldeportal → Erweitert → Reverse Proxy (Bezeichnungen können je DSM-Version abweichen):

| Einstellung | Wert |
|---|---|
| Quelle | HTTPS, Hostname `accounts.softchem.netz`, Port 443 |
| Ziel | HTTP, Hostname `127.0.0.1`, Port 8000 |
| Host | ursprünglichen Host `accounts.softchem.netz` erhalten |
| X-Forwarded-Proto | vom Proxy auf `https` setzen/überschreiben |
| X-Forwarded-For | durch den Proxy aus der tatsächlichen Verbindung bilden |

Das Backend bleibt an `127.0.0.1:8000` gebunden. Keine weitere Portweiterleitung auf 8000,
kein öffentlicher alternativer HTTP-Zugang. HTTP-Port 80 höchstens für Weiterleitung auf HTTPS.
Header vom Client dürfen nicht unverändert als vertrauenswürdige Proxy-Angaben durchgereicht
werden. Andere virtuelle Hosts dürfen nicht auf dieses Backend weiterleiten.

## 3. Anwendung konfigurieren

In der lokalen `.env` (keine Geheimnisse):

```dotenv
KW_PUBLIC_URL=https://accounts.softchem.netz
KW_COOKIE_SECURE=1
KW_TRUSTED_PROXY_IPS=<tatsächliche-Proxy-IP-aus-Sicht-des-Containers>
```

Der letzte Eintrag ist ein Platzhalter. Uvicorn übernimmt Forwarded-Header nur von diesen
Adressen; niemals `*` einsetzen. Docker kann den lokalen DSM-Zugang auf seine Bridge-Gateway-IP
umsetzen. Die Gateway-Adressen des laufenden Containers als Ausgangspunkt ermitteln:

```bash
docker inspect "$(docker compose ps -q app)" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{println}}{{end}}'
```

Nur die tatsächlich für DSM verwendete Adresse eintragen und den Proxy-Test unten durchführen.
Nach Änderungen am Docker-Netz diese Einstellung erneut prüfen. Ein Container im gleichen Netz
darf keinen direkten Backend-Zugang erhalten, wenn er nicht ebenfalls als vertrauenswürdig gilt.

```bash
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health
```

Ohne `KW_PUBLIC_URL` bleibt der bisherige lokale Betrieb möglich. Mit gesetzter URL lehnt die
Anwendung HTTP und andere Hostnamen mit 400 ab; `/health` bleibt für den lokalen Healthcheck
erreichbar. Es gibt keine HTTP-Weiterleitung mit übermittelten Passwörtern. HTTPS-Antworten
tragen HSTS für ein Jahr, ohne `includeSubDomains`/Preload. `KW_COOKIE_SECURE=0` ist zusammen mit
einer öffentlichen URL ungültig.

## 4. Abnahme und Rückfall

1. `https://accounts.softchem.netz/login` mit vertrauenswürdiger Zertifikatskette öffnen.
2. Anmeldung, Passwortwechsel, Abmelden und ein CSRF-geschütztes Formular prüfen. Cookie
   `kw_session`: `Secure`, `HttpOnly`, `SameSite=Lax`; kein Redirect auf HTTP/fremde Hosts.
3. Direkter Aufruf von `http://127.0.0.1:8000/login` liefert 400; `/health` liefert weiterhin 200.
   Port 8000 ist von anderen Geräten nicht erreichbar.
4. Ein selbst gesetztes `X-Forwarded-Proto: https` von einem nicht vertrauten Client verschafft
   keinen Zugang. Falls die Loginseite über DSM 400 liefert, zuerst Proxy-IP und Header prüfen;
   die Vertrauensliste nicht pauschal erweitern.
5. Zertifikatsverlängerung und DNS-Verantwortlichen in der Betriebsdokumentation festhalten.

Bei Fehlkonfiguration den Netzwerkzugang beschränken, DSM-/Header-Konfiguration korrigieren und
den Container neu erstellen. Für lokalen Notbetrieb kann `KW_PUBLIC_URL` entfernt und das Cookie
auf `auto` gesetzt werden; dieser HTTP-Zugang bleibt ausschließlich lokal. Bereits gespeichertes
HSTS im Browser beachten. Keine Änderung am bekannten Initialpasswort durch dieses Paket.

## Quellen

- [Uvicorn: Proxy-Header und vertrauenswürdige Adressen](https://www.uvicorn.org/settings/#http)
- [Synology: Reverse Proxy](https://kb.synology.com/en-global/DSM/help/DSM/AdminCenter/application_appportalias?version=7)
