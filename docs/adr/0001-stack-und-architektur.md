# 0001 – Stack und Architektur

- **Status:** Accepted (2026-11-02)

## Kontext
Zwei Kunden (CG, CVH) mit Entra und On-Prem-AD, rund 650 Konten je Mandant, kleines Team, Betrieb
auf einem Docker-Host. Wenig Code, wenig bewegliche Teile.

## Entscheidung
Ein Container: Python 3.12, FastAPI + Jinja2 + HTMX (ohne JS-Build), SQLite via SQLAlchemy +
Alembic, APScheduler im App-Prozess, Caddy als Reverse-Proxy (Phase 5). Mandanten in einer
Instanz, getrennt über `tenant_id` in allen Tabellen.

## Alternativen
- **SPA mit separatem Backend:** Build-Kette, mehr Code, kein Mehrwert für eine Listenansicht.
- **Postgres von Beginn an:** zweiter Dienst ohne Bedarf; SQLAlchemy hält den Umstieg offen.
- **Getrennte Instanz je Kunde:** doppelter Betrieb; die Trennung per `tenant_id` genügt, solange
  nur Softchem-IT zugreift (offene Entscheidung zur Kundensicht beachten).

## Konsequenzen
SQLite erlaubt nur einen Schreiber; Läufe laufen darum nacheinander mit Sperre. Skalierung über
einen Host hinaus ist kein Ziel.
