# 0010 – HTTPS über DSM mit fester öffentlicher Origin

- **Status:** Accepted (2026-11-09); technische Vorbereitung, Live-Abnahme ausstehend

## Kontext

Phase 5 ist freigegeben. Niklas nennt `accounts.softchem.netz` als Zieladresse; der Betrieb
erfolgt auf der Synology. Die Anwendung muss Host und HTTPS zuverlässig erkennen, damit
Anmeldung, CSRF-Schutz und der folgende OIDC-Callback dieselbe Origin verwenden.

## Entscheidung

- Als Betriebsvariante DSM-Reverse-Proxy vorbereiten. Die vorhandene Synology übernimmt TLS
  und Zertifikatsverwaltung; das App-Backend bleibt ausschließlich an Loopback gebunden.
  Dies konkretisiert die bisher offene Proxy-Wahl aus ADR 0004 für diesen Ausbau.
- `KW_PUBLIC_URL` bezeichnet ausschließlich die öffentliche HTTPS-Origin ohne Pfad. Bei
  gesetzter URL werden fremde Hosts und HTTP abgelehnt. Der lokale Healthcheck bleibt möglich.
- Uvicorn verarbeitet Proxy-Header ausschließlich von `KW_TRUSTED_PROXY_IPS`, das Compose als
  `FORWARDED_ALLOW_IPS` durchreicht. Keine eigene unbeschränkte Header-Auswertung in der App.
- HTTPS-Antworten tragen HSTS ohne Subdomain-/Preload-Vorgabe. Sitzungs-Cookies müssen sicher
  transportiert werden; ein explizites `KW_COOKIE_SECURE=0` mit öffentlicher URL ist ungültig.
- Bestehender lokaler Betrieb bleibt bei leerer öffentlicher URL verfügbar.

## Alternativen und Konsequenzen

Caddy wäre eine mögliche spätere Proxy-Variante mit gleicher App-Schnittstelle. DSM benötigt
keinen weiteren Container. DNS, passendes Zertifikat, Vertrauen der Clients und konkrete
Proxy-IP sind Deploymentparameter; ihre Einrichtung und Live-Abnahme stehen aus.
Für den genannten Host wird keine öffentliche ACME-Ausstellung vorausgesetzt.
Anleitung und Abnahme: `docs/https-setup.md`. Netzwerkbindung/Firewall bleiben zusätzlich zur
Anwendungsprüfung erforderlich; ein vertrauenswürdiger Proxy ist Teil der Sicherheitsgrenze.
