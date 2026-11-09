# Betrieb, Updates und Abnahme von Phase 5

Stand: 9. November 2026. Der vorhandene Livebetrieb ist durch Niklas bestätigt. Diese Anleitung
betrifft den Ausbau; DNS, Zertifikate, reale Entra-Werte und externe Sicherungsziele werden beim
Deployment eingerichtet. Der Code führt keine automatische Live-Aktivierung aus.

## Zuständigkeiten und Vorlagen

| Thema | Entscheidung/Einrichtung | Anleitung |
|---|---|---|
| HTTPS | DSM-Proxy für `accounts.softchem.netz`, DNS, Zertifikatskette und konkrete Proxy-IP | [https-setup.md](https-setup.md) |
| SSO | bestehende Mail-App um Web-Login erweitern; Tenant-/Client-/Gruppen-IDs und Secret-Referenz einsetzen | [sso-setup.md](sso-setup.md) |
| Externes Backup | zweites Ziel, Notfallzugänge, RPO/RTO und Aufbewahrung festlegen; bis dahin deaktiviert | [external-backup.md](external-backup.md) |
| Zertifikate | öffentliche Zertifikate hinterlegen, Zuständigkeit für Erneuerung und Inventarpflege | unten |
| Update | Code/Image-Stand dokumentieren, Sicherung prüfen, gezielt abnehmen | unten |

Entwicklung liefert Code, Migrationen, Vorlagen und technische Prüfungen. Niklas setzt die
Betriebswerte ein und nimmt die Einrichtung auf der Synology ab. Keine Schlüssel/Passwörter
ins Repository, in `.env`, in Tickets oder in Chat-Nachrichten kopieren.

## Zertifikate und CG-Ausnahme

`config/certificates.example.yaml` nach `config/certificates.yaml` kopieren. Öffentliche
PEM-/DER-Zertifikate unter `config/certificates/` hinterlegen und mit technischem Namen,
relativem Dateipfad und `warn_days` (Standard 30) eintragen. Optional anderer Inventarpfad
über `KW_CERTIFICATES_FILE`. Private Schlüssel bleiben Docker-Secrets bzw. in DSM.

Die Übersicht warnt bei bevorstehendem Ablauf, abgelaufenen, noch nicht gültigen und
unlesbaren Zertifikaten; **Läufe** zeigt den Bestand. Fehlendes optionales Inventar bedeutet
noch keine Überwachung. Ungültige Struktur/Pfade brechen den Start ab; eine später fehlende
einzelne Datei erscheint als Warnung. Die Anzeige prüft die zeitliche Gültigkeit, keine
Zertifikatskette oder tatsächliche TLS-Erreichbarkeit.

Mail-/SSO-Zertifikat, DSM-HTTPS-Zertifikat und benötigte DC-Zertifikate ausdrücklich aufnehmen.
Eine CA-Datei überwacht den Ablauf dieser CA, nicht automatisch die von ihr ausgestellten
DC-Zertifikate. Nach Rotation sowohl Entra/DSM/DC als auch Secret-Referenzen, Fingerabdruck und
öffentliche Inventardatei aktualisieren; anschließend jeweilige Verbindung/Anmeldung prüfen.

Für CG gilt KI-13/ADR 0005: tatsächliches `accept_weak_dc_keys_until` in der privaten
Mandantenkonfiguration und ersten DC-Zertifikatsablauf prüfen; **spätestens vor 1. Januar 2027**
neu entscheiden, bei früherem konfiguriertem Enddatum entsprechend früher. Ohne reale Werte
wird kein Termin als erledigt oder verlängert vermerkt. Die bestehende Ausnahme bleibt
befristet und mandantenbezogen; TLS-Prüfung bleibt aktiv.

## Update und Rückfall

1. Vorherigen Git-Commit und Image-ID sowie Version von DSM/Container Manager und Plattform
   im geschützten Betriebshandbuch erfassen. Lokale `.env`, Override-Datei, Konfiguration und
   Secret-Wiederherstellungszugänge verfügbar halten; echte Werte gehören nicht ins Repo.
2. Vor dem Update eine geprüfte Sicherung über `python -m app.cli backup` erstellen, Erfolg und
   Schreibrechte von `/backup` kontrollieren. Bei Fehlern nicht mit dem Update fortfahren.
3. Freigegebenen Code holen und Image bauen. Schema `0009` ergänzt SSO-Referenzen, ohne lokale
   Benutzer/Entscheidungen zu ersetzen. Der App-Start versucht zusätzlich die bisherige Sicherung
   vor Migrationen; deren Fehlermeldung ersetzt die manuelle Prüfung aus Schritt 2 nicht.
4. Container neu erstellen, `/health` und Server-Logs prüfen. Der neue Start über `app.server`
   formatiert bereits die ersten Uvicorn-Zeilen als JSON und akzeptiert keine universelle
   Proxy-Vertrauensliste. Es bleibt bei einem App-Prozess.
5. Lokalen Admin-Zugang, SSO nur bei Aktivierung, HTTPS, eine Kontenansicht, CSRF-Formular,
   Tagesabruf und Sicherungsstatus gezielt kontrollieren. Mail-App bei SSO-Erweiterung mit prüfen.
6. Bei Problemen zuerst Zugriff begrenzen und Funktion/Config zurücknehmen. Ein altes Image
   nicht ungeprüft mit neuerem Schema starten: App stoppen, vorigen Image-/Konfigurationsstand
   und dazu passende Sicherung über den dokumentierten Restore-Weg wiederherstellen.
   Restore verwirft alte Sitzungen; danach neu anmelden. Ein zuvor abgemeldetes Browsercookie
   wird durch Wiederherstellung eines älteren Datenstands nicht wieder gültig.

```bash
docker compose exec app python -m app.cli backup
docker compose up -d --build
curl --fail http://127.0.0.1:8000/health
docker compose logs --tail=30 app
```

Keine Zugriffe auf Quellen durchführen, deren Secrets/Verbindungen noch nicht eingerichtet sind.
Externe Backups bleiben ausdrücklich aus, bis das zweite Ziel verfügbar ist.

## CI und Sicherheitswartung

CI prüft Format, Lint, Typen, Tests (Untergrenze 95 %), `pip-audit`, Container-Neustart,
Nicht-root und schreibgeschütztes Dateisystem. Ein echtes Restic-Binary aus dem Docker-Build
prüft Verschlüsselung, Integrität, Rückholung und separate Datenbank-Wiederherstellung.
Trivy erzeugt eine CycloneDX-SBOM mit vollständigem Schwachstellenbestand. Hohe/kritische
Befunde mit verfügbarem Fix blockieren den Container-Job. Auch nicht behebbare Befunde bleiben
im vollständigen Bericht sichtbar und werden bewertet. Berichte werden 14 Tage als CI-Artefakte
gespeichert; nicht als Dateien mit Kunden-/Laufzeitdaten im Repository.

Python- und Go-Abhängigkeiten/Compiler separat beachten. Restic ist ein Sicherheitsbuild aus
geprüftem Upstream-Code, siehe [Build-Provenienz](../docker/restic/README.md). Nach einem
Abhängigkeitsupdate sämtliche zugehörigen Lockfiles mit Hashes konsistent halten.

Dependabot-Sichtung am 9. November 2026, jeweils letzter PR-CI-Lauf auf dem gelisteten Head:

| PR | Paket | Head | Ergebnis |
|---|---|---|---|
| 23 | dnspython | `7cfb0bd` | CI grün |
| 24 | ruff | `d349169` | CI grün |
| 25 | pydantic | `29b2a82` | CI rot: 2.14.3 benötigt pydantic-core 2.50.4, Lockfiles halten 2.50.2 |
| 26 | sqlalchemy | `c0a92ae` | CI grün |
| 27 | pip | `1296425` | CI grün |

Dies ist eine Momentaufnahme und keine Merge-Freigabe. Kein Dependabot-PR wurde hier gemergt.
PR 25 benötigt ein konsistentes Update der transitiven Abhängigkeit; danach CI neu prüfen.
Der Phase-5-Stand behält die bisherigen Python-Versionen bei.

## Abschlussnachweise

- [ ] DSM-HTTPS inklusive Header-/Portprüfung auf der Synology abgenommen.
- [ ] SSO mit zugelassenem/abgelehntem Benutzer, Rollen-/Gruppenentzug und lokalem Notzugang abgenommen.
- [ ] Zertifikatsinventar mit realen öffentlichen Dateien gepflegt; CG-Frist geprüft.
- [ ] Externes Ziel, verschlüsselte Ablage und unabhängiger Notfallzugang eingerichtet.
- [ ] Vollständiger externer Restore auf getrenntem System einschließlich gemessener RPO/RTO abgenommen.

Externe Sicherung und ihre Live-Abnahme sind bis zur Zielbereitstellung bewusst zurückgestellt.
Technische Entwicklung und lokale Tests allein schließen diese Nachweise nicht ab.
