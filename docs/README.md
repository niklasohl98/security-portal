# Dokumentation

Diese Seite ist der Einstieg in die Dokumentation des Kontenwächters. Jede Information hat eine
bevorzugte Quelle; dadurch bleiben Anleitungen auffindbar und widersprechen sich nicht.

## Ich möchte …

| Ziel | Einstieg |
|---|---|
| das Tool lokal starten | [Schnellstart](../README.md#schnellstart) |
| das Tool bedienen | [Nutzerhandbuch](user-guide.md) |
| einen Kunden-Tenant anbinden | [Entra](entra-setup.md) · [Active Directory](ad-setup.md) |
| HTTPS oder SSO einrichten | [DSM-HTTPS](https-setup.md) · [Entra-SSO](sso-setup.md) |
| Mailberichte einrichten | [Mail-Bericht](mail-setup.md) |
| Sicherungen betreiben oder wiederherstellen | [Lokaler Betrieb](operations.md) · [externes Backup](external-backup.md) |
| eine Einstellung nachschlagen | [Konfigurationsreferenz](configuration.md) |
| einen Wartungsbefehl ausführen | [CLI-Referenz](cli.md) |
| Daten oder Audit-Ereignisse nachschlagen | [Datenmodell](data-model.md) · [Audit-Log](audit-log.md) |
| den Aufbau verstehen | [Architektur](architecture.md) · [Entscheidungen](adr/README.md) |
| am Code arbeiten | [Entwicklungsleitfaden](development.md) · [Beitragen](../CONTRIBUTING.md) · [Dokumentationsstandard](documentation.md) |
| Produktumfang und Reihenfolge verstehen | [Roadmap](../ROADMAP.md) · [ursprüngliches Fachkonzept](plan.md) |
| Risiken oder Sicherheitsgrenzen prüfen | [Known Issues](../KNOWN_ISSUES.md) · [Security](../SECURITY.md) |

## Dokumentationsarten

- **README:** Produktüberblick, Status und kürzester Weg zu einer laufenden Entwicklungsinstanz.
- **Anleitungen:** konkrete, in Reihenfolge ausführbare Einrichtung oder Wiederherstellung. Jeder
  Schritt nennt Voraussetzungen, Prüfung und Rückfall.
- **Referenzen:** vollständige Beschreibung von Konfiguration und CLI. Diese Seiten sind die
  kanonische Nachschlagequelle und werden automatisiert mit dem Code abgeglichen.
- **Architektur:** tatsächlich implementierter Aufbau, Datenfluss und Sicherheitsgrenzen.
- **ADRs:** warum eine tragende Entscheidung getroffen wurde. Sie werden fortgeschrieben oder durch
  eine neue ADR ersetzt, nicht rückwirkend umgeschrieben.
- **Roadmap/Plan:** geplanter beziehungsweise historischer Produktumfang. Geplante Funktionen sind
  keine Beschreibung des aktuellen Codes.
- **Betrieb:** Update, Zertifikate, Sicherung, Wiederanlauf und noch offene Live-Abnahmen.

## Verbindlicher Stand

Der Code und seine Tests bestimmen das tatsächliche Verhalten. Die Architektur beschreibt diesen
Stand in Worten. `user-guide.md`, `configuration.md`, `cli.md`, `data-model.md` und `audit-log.md`
sind die vollständigen Bedien- und Referenzsichten.
`CHANGELOG.md` erklärt Änderungen zwischen Ständen; `ROADMAP.md` beschreibt die Zukunft.
`docs/handover-codex.md` ist eine historische Übergabe vom 9. November 2026 und keine laufende
Referenz.

Interne Links sowie die Abdeckung aller `KW_*`-Variablen, CLI-Befehle, Webrouten,
Datenbanktabellen und Audit-Codes werden in der Testsuite geprüft. Wie neue Dokumentation aufgebaut
und abgenommen wird, steht in
[documentation.md](documentation.md).
