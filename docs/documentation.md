# Dokumentationsstandard

Dokumentation ist Teil des Produkts. Eine Änderung ist erst fertig, wenn Betrieb, Nutzer und die
nächste entwickelnde Person das Verhalten ohne Wissen aus Chat oder PR nachvollziehen können.

## Qualitätskriterien

Jede neue Funktion beantwortet an den passenden Stellen:

1. **Zweck und Grenze:** Welches Problem löst sie, was tut sie ausdrücklich nicht?
2. **Bedienung:** Wie wird sie aktiviert oder aufgerufen? Welche Defaults gelten?
3. **Sicherheit:** Welche Daten, Rechte, Secrets und Fehlerbilder sind beteiligt?
4. **Betrieb:** Wie wird Erfolg geprüft, wie überwacht und wie zurückgerollt?
5. **Architektur:** Welche Komponenten, Datenflüsse und persistierten Zustände kommen hinzu?
6. **Entscheidung:** Warum wurde bei einer tragenden Wahl diese Variante genommen?
7. **Änderung:** Was muss ein Betreiber beim Update wissen?

Nicht jede Antwort gehört in eine neue Datei. Der [Dokumentationsindex](README.md) legt die
bevorzugte Quelle fest. Andere Seiten verlinken dorthin, statt vollständige Referenztabellen zu
kopieren.

## Schreibregeln

- Tatsächlich implementiertes Verhalten im Präsens; Zukunft nur in Roadmap oder ausdrücklich als
  geplant markieren.
- Befehle müssen kopierbar sein. Gefährliche oder zustandsändernde Befehle nennen Voraussetzung,
  Auswirkung und Erfolgskontrolle unmittelbar davor oder danach.
- Defaults, Grenzwerte, Dateipfade und Fehlercodes exakt aus dem Code übernehmen.
- Beispiele verwenden ausschließlich erfundene Werte und reservierte Domains.
- Keine Secrets, Tokens, realen IDs, Mailadressen oder Personendaten dokumentieren.
- Relative Links innerhalb des Repositories; externe Quellen nur für maßgebliche Hersteller- oder
  Standardsdokumentation.
- Eine Abkürzung beim ersten Auftreten erklären. Überschriften beschreiben Aufgaben oder Inhalte.
- Historische Übergaben und Pläne klar als historisch kennzeichnen; sie sind keine Betriebsquelle.

## Welche Datei ändert sich wann?

| Änderung | Pflichtdokumentation |
|---|---|
| neue oder geänderte Umgebungsvariable | `.env.example`, `configuration.md`, betroffene Anleitung |
| neues Feld in `tenants.yaml` | `config/tenants.example.yaml`, `configuration.md`, betroffene Anleitung |
| neuer CLI-Befehl | `cli.md`; bei Betriebsrelevanz zusätzlich die passende Anleitung |
| neue oder geänderte Webroute | `user-guide.md`; bei Betriebsrelevanz zusätzlich `operations.md` |
| neue oder geänderte Datenbanktabelle | `data-model.md`, Migration und Architektur |
| neuer oder geänderter Audit-Code | `audit-log.md`; bei Sicherheitsbezug zusätzlich `SECURITY.md` |
| neue Nutzerfunktion | README-Status, Architektur, Changelog und Bedien-/Betriebsanleitung |
| neue Sicherheitsgrenze oder Berechtigung | `SECURITY.md`, Architektur und gegebenenfalls ADR |
| tragende technische/produktbezogene Entscheidung | neue ADR und Verweis aus Architektur/Roadmap |
| neues Betriebsrisiko oder bewusste Einschränkung | `KNOWN_ISSUES.md` |
| behobenes Nutzer- oder Betriebsproblem | `CHANGELOG.md`; Known Issue schließen, falls vorhanden |

## Automatische Abnahme

`tests/test_documentation.py` prüft:

- alle relativen Markdown-Links auf vorhandene Ziele;
- jede in App-Code oder `.env.example` vorkommende `KW_*`-Variable auf einen Eintrag in der
  Konfigurationsreferenz;
- jeden von `python -m app.cli --help` angebotenen Befehl auf einen Eintrag in der CLI-Referenz;
- jede statisch definierte Webroute auf einen Eintrag im Nutzerhandbuch;
- jede SQLAlchemy-Tabelle auf einen Eintrag in der Datenmodellreferenz;
- jeden statisch definierten Audit-Code auf einen Eintrag im Audit-Katalog;
- jede öffentliche Klasse und Funktion auf Modulebene auf einen Docstring (Migrationen ausgenommen).

Diese Prüfungen ersetzen keine inhaltliche Review. Vor einem PR zusätzlich kontrollieren:

- Stimmen Anleitung und tatsächlicher Ablauf einschließlich Fehlerfall und Rückfall überein?
- Ist klar getrennt, was implementiert, konfiguriert und live abgenommen ist?
- Kann eine fachkundige Person die Änderung ausschließlich mit der Repository-Dokumentation
  betreiben und warten?
