# 0009 – Ausbau zu IAM-Governance und Lizenzmanagement

- **Status:** Accepted (2026-11-09, strategische Richtung durch Niklas vorgegeben)
- **Umsetzungsstand:** geplant; keine Freigabe für Schreibzugriffe auf Kundenverzeichnisse

## Kontext

Der Kontenwächter liest Konten und Lizenzen aus Entra ID und AD, führt korrespondierende
Konten zusammen und bewertet Inaktivität. Niklas bestätigt am 9. November 2026 den
einwandfreien Livebetrieb und beauftragt eine Entwicklungsroadmap zur strategischen
Weiterentwicklung als IAM- und Lizenzmanagementlösung. Entra-SSO, HTTPS und externe Backups
sollen in diese Planung einfließen.

Die bestehende Datenbasis enthält noch kein Vertrags-/Preismodell, keine vollständige
Berechtigungsübersicht und keine Prozesse für Genehmigungen oder den Identitätslebenszyklus.
Die Zuordnung zweier Verzeichniskonten bildet außerdem nicht sämtliche Konten einer Person ab.

## Entscheidung

- Ausbau auf dem bestehenden Stack und zunächst für die Softchem-IT mit CG und CVH als Pilot.
  Die verbindliche Planungsreihenfolge und Abnahmekriterien stehen in `ROADMAP.md`.
- Betriebsschutz vervollständigen: HTTPS, Entra-OIDC mit Gruppensteuerung und kontrolliertem
  lokalem Notzugang, externe verschlüsselte Backups mit erprobter Wiederherstellung.
- Gemeinsame Grundlage für Bestände, Historie, Datenqualität und stabile Quellenzuordnung
  schaffen; Migrationen erhalten vorhandene Ausnahmen und Entscheidungen.
- Danach Lizenzbestand, Zuweisungen, Preise, Verträge und Optimierungskandidaten liefern.
  Vertraglich gebundene Kosten, kalkulatorisches Potenzial, freie Kapazität und realisierte
  Einsparungen werden getrennt ausgewiesen.
- Anschließend IAM-Bestand erweitern: Personen-/Kontenbezug, Gruppen, Rollen, technische
  Identitäten und Verantwortliche. Neue Datenquellen benötigen geprüfte minimale Leserechte.
- Governance mit Aufgaben, Zugriffsprüfungen, Anträgen und Eintritt/Wechsel/Austritt ergänzen.
  Entscheidungen und extern umgesetzte Änderungen müssen nachvollziehbar verifiziert werden.
- Aktive Änderungen in Entra/AD sind eine spätere **Entscheidungsoption**, keine beschlossene
  Funktion. Dafür wären eine eigene fachliche Freigabe, ein neues ADR, angepasste Projektregeln,
  getrennte Schreib-Connectoren und ein begrenzter Test-/Produktivpilot erforderlich.

## Geltung bestehender Entscheidungen

Dieses ADR erweitert das Produktziel aus `docs/plan.md`. Es hebt den lesenden Zugriff aus
`CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md` und dem PR-Template nicht auf. ADR 0001 bis 0008
bleiben für den vorhandenen Betrieb gültig; die dortige MVP-Planung wird durch die Roadmap
ergänzt. Die vorhandene 30-Tage-Regel, Mandantentrennung und Secret-Verwaltung bleiben bestehen.

Für Kundenportal, HR-Anbindung und zusätzliche Anbieter ist kein Umfang beschlossen.
Eine Infrastrukturänderung (z. B. Postgres/Worker) folgt belegtem Bedarf und eigenem ADR.

## Betrachtete Alternativen

- **Nur Inaktivität ausbauen:** würde das beauftragte IAM-/Lizenzmanagementziel nicht abdecken.
- **Sofort Provisionierung und Entzug automatisieren:** erweitert Berechtigungen und mögliche
  Auswirkungen, bevor belastbare Daten und Genehmigungsprozesse vorhanden sind. Zunächst
  Bestandsübersicht und Governance liefern; Ausführung gesondert entscheiden.
- **Vollständige Neuentwicklung mit zusätzlicher Infrastruktur:** derzeit kein belegter Bedarf;
  bestehende Connectors, Tests und Betrieb bilden eine nutzbare Grundlage.

## Konsequenzen

- Neue Funktionen werden als einzelne Lieferpakete mit Voraussetzungen und Abnahme geplant.
  Der bestätigte Livebetrieb wird nicht als offene allgemeine Abnahme zurückgestuft.
- Kostenangaben brauchen gepflegte Preis-/Vertragsdaten. Eine entfernte Zuweisung allein belegt
  keine reduzierte Rechnung; Kontoanmeldungen belegen keine produktspezifische Nutzung.
- Personenbezogene Historie und Berechtigungsdaten brauchen festgelegte Aufbewahrung,
  Verantwortlichkeiten, Zugangskontrolle und überprüfbare Mandantentrennung.
- Details wie führende Mandantenkonfiguration, Historienmodell und spätere Ausführung erhalten
  eigene ADRs vor ihrer Implementierung. Termine folgen konkretem Umfang und Kapazität.
