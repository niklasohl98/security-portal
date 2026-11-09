# Entwicklungsroadmap: IAM und Lizenzmanagement

Stand: 9. November 2026. Strategisches Ziel von Niklas: den Kontenwächter zu einer
mandantenfähigen IAM- und Lizenzmanagementlösung entwickeln. Grundentscheidung:
[ADR 0009](docs/adr/0009-iam-und-lizenzmanagement.md).

Diese Roadmap beschreibt geplante Lieferpakete. Den implementierten Stand beschreibt
[docs/architecture.md](docs/architecture.md); das ursprüngliche MVP-Konzept steht in
[docs/plan.md](docs/plan.md). Jede Phase endet mit Vorführung, grünen Prüfungen und Abnahme
durch Niklas. Nur ausdrücklich abgeschlossene Funktionen gelten als geliefert.

## 1. Zielbild und Leitplanken

Die Softchem-IT soll je Mandant beantworten können:

- Welche Personen, Konten, Gruppen und Berechtigungen gibt es, und wer ist verantwortlich?
- Welche Lizenzen sind vorhanden, zugewiesen und vertraglich gebunden; was kosten sie?
- Welche Zugriffe und Lizenzen sind zu prüfen, weshalb und auf welcher Datenbasis?
- Wer entscheidet über eine Maßnahme, bis wann und wie wird ihre Umsetzung nachgewiesen?

Der Ausbau beginnt mit Bestandsübersicht, Kosten und Governance: Verantwortlichkeiten,
Prüfungen und dokumentierte Entscheidungen. Eine spätere aktive Verwaltung wird in Phase 10
gesondert bewertet. Bis dahin bleiben alle Zugriffe auf Kunden-Entra und AD ausschließlich
lesend; die Schreibschutzregeln aus `CLAUDE.md` und `SECURITY.md` gelten unverändert.
Der gesonderte Mailversand nach ADR 0008 bleibt davon unberührt.

Planungsrahmen:

- Zunächst Microsoft 365 / Entra ID und On-Prem-AD, Pilot mit CG und CVH, Nutzer Softchem-IT.
  Kundenportal und zusätzliche Anbieter werden bei konkretem Bedarf bewertet.
- Entra und AD bleiben führend für ihre Verzeichnisdaten. Das Tool führt eigene Preise,
  Vertragsdaten, Verantwortlichkeiten, Bewertungen und Entscheidungen mit Herkunftsnachweis.
- Die bestehende 30-Tage-Regel bleibt bestehen. Zusätzliche IAM- und Lizenzregeln erhalten eigene,
  versionierte Kriterien und ersetzen keine bestehenden Regeln stillschweigend.
- Jeder Befund zeigt Quelle, Datenstand, Begründung und Unsicherheiten. Fehlende Daten bedeuten
  „unbekannt“; eine Kontoanmeldung belegt keine Nutzung eines bestimmten Lizenzprodukts.
- Ausnahmen, Dienstkonten, Ressourcen und Notfallkonten werden vor Maßnahmen berücksichtigt.
  Personen und Konten bleiben unterscheidbar: eine Person kann mehrere Konten besitzen.
- Bestehenden Stack und Synology-Betrieb schrittweise erweitern. Neue Infrastruktur folgt
  gemessenem Bedarf und einer Architekturentscheidung.

## 2. Ausgangslage

**Livebetrieb:** von Niklas am 9. November 2026 als einwandfrei bestätigt. Ein erneuter allgemeiner
Abnahmelauf ist kein vorgeschaltetes Arbeitspaket. Neue Funktionen werden gezielt abgenommen;
genaue Betriebsparameter und einzelne Integrationen werden bei Bedarf dokumentiert. Die
Bestätigung schließt offene Härtungsaufgaben nicht automatisch.

| Bisherige Phase | Stand |
|---|---|
| 1 Grundgerüst | abgeschlossen: Docker, FastAPI, Datenbank, Mandantenkonfiguration |
| 2 Entra | abgeschlossen und auf der Synology abgenommen am 3. November 2026 |
| 3 AD | abgeschlossen; Liveabgleich am 5., Abnahme am 6. November 2026 |
| 4 Logik und Oberfläche | abgeschlossen am 6. November 2026: Merge, Regeln, Prüfliste, Ausnahmen, Export, täglicher Abruf |
| 5 Härtung und Ausbau | teilweise umgesetzt: lokale Anmeldung, Audit-Log, Backups, Benutzerverwaltung, Graph-Mailberichte; restliche Pakete unten |

Technische Basis vor dieser Planung: `main` = `775e14b`; letzte Funktionsänderung in `7105db0`.
Lokal geprüft: 684 Tests, 98,67 % Abdeckung, Format/Lint/Typen grün. Dies belegt den bisherigen
Code; die folgenden Funktionen und die Livekonfiguration sind damit nicht geprüft.

## 3. Reihenfolge und Meilensteine

P0 = Betriebsschutz mit Vorrang, P1 = nächster Produktausbau, P2 = darauf aufbauend,
P3 = bedarfsabhängige Erweiterung. Teilpakete stehen in ihrer vorgesehenen Reihenfolge.

| Phase | Priorität | Lieferbares Ergebnis | Voraussetzung | Status |
|---|---|---|---|---|
| 5 Betriebsschutz abschließen | P0 | HTTPS, Entra-SSO, geprüfter externer Wiederanlauf | Betriebsentscheidungen und Softchem-App | technisch vorbereitet; Deployment-/Live-Abnahme ausstehend |
| 6 Verlässliche Datenbasis | P1 | nachvollziehbare Bestände, Historie, Konfiguration und Datenqualität | bestehende Connectors; Phase 5 vor regulärer Freigabe | geplant |
| 7 Lizenzmanagement | P1 | Bestands- und Kostencockpit, Vertragsfristen, belegbare Optimierungskandidaten | Phase 6; Preis-/Vertragsdaten für Kosten | geplant |
| 8 IAM-Transparenz | P2 | Personen-/Kontenbezug, Gruppen, privilegierte Zugriffe und Risiken | Phase 6, freigegebene zusätzliche Leserechte | geplant |
| 9 Governance und Lebenszyklus | P2 | wiederkehrende Zugriffsprüfungen, Anträge und Eintritt/Wechsel/Austritt | Phasen 7 und 8, Verantwortliche und Rollen | geplant |
| 10 Kontrollierte Ausführung | P3 | separat entscheidbarer Pilot für freigegebene Änderungen | Phase 9 und eigene Freigabe der neuen Produktgrenze | Entscheidungsoption |
| 11 Integration und Skalierung | P3 | Schnittstellen und nach Bedarf verteilbarer Betrieb | gemessener Bedarf; kann vor Phase 10 erforderlich werden | bedarfsabhängig |

Phase 5 und die Entwicklung von Phase 6 können parallel vorbereitet werden. Zuerst wird
Phase 7 produktisiert; Phase 8 verwendet dieselbe Datenbasis. Phase 10 ist keine Voraussetzung
für eine nutzbare IAM-Governance- und Lizenzmanagementlösung. Ihre Nummerierung verpflichtet
nicht zur Umsetzung. Vor mehreren Prozessen/Workern greift bereits Phase 11.

Kalendertermine und Aufwand werden je Lieferpaket nach Umfangsklärung und verfügbarer Kapazität
geschätzt. Verbindliche Reihenfolge und überprüfbare Meilensteine gehen einer Terminprognose vor.
Ein fester betrieblicher Termin besteht bereits: CG-Schlüsselausnahme **vor 1. Januar 2027**
neu bewerten; tatsächliches Ausnahme-Enddatum und Zertifikatsablauf können früher liegen (KI-13).

## 4. Phase 5 – Betriebsschutz abschließen

**Ergebnis:** Der funktionierende Betrieb erhält einen dokumentierten, wiederherstellbaren
und zentral abgesicherten Zugang.

**Entwicklungsstand 9. November 2026:** HTTPS für `accounts.softchem.netz` über DSM,
SSO über die vorhandene Mail-App (eigene Einstellungen/Platzhalter), externe verschlüsselte
Sicherung und Betriebspflege implementiert. SSO bleibt bis zur Konfiguration deaktiviert.
Externe Sicherung bleibt nach Vorgabe von Niklas bis zur Bereitstellung eines zweiten Ziels
deaktiviert; ihre Live-Abnahme ist zurückgestellt. Einrichtung und offene Nachweise:
[docs/operations.md](docs/operations.md). Technische Vorbereitung schließt die Phase noch nicht
als im Livebetrieb abgenommen ab.

1. **5.1 HTTPS:** DSM-Reverse-Proxy oder Caddy entscheiden (ADR 0004), URL und Proxy-Vertrauen
   dokumentieren, TLS einrichten, `KW_COOKIE_SECURE=1`, direkte Umgehung des Proxys verhindern.
2. **5.2 Entra-SSO:** OIDC im Softchem-Tenant, Zugang über freigegebene Sicherheitsgruppe,
   MFA/Conditional Access in Entra; lokale Anmeldung als kontrollierter Notzugang.
   App-Registrierung, Rücksprung-URL und Gruppe sind betriebliche Voraussetzungen.
   Abmelden, Gruppenentzug, Sitzungsablauf und Aussperrung berücksichtigen.
3. **5.3 Externe Backups:** Ziel außerhalb der Synology wählen, Transport und Ablage verschlüsseln,
   Schlüssel und Wiederherstellungszugang getrennt sichern, Ausfälle melden. RPO/RTO mit Niklas
   festlegen und messen; Vorschlag für den Pilot: maximal 24 Stunden Datenverlust, 4 Stunden
   Wiederanlauf. Vor Migrationen bleiben lokale Sicherungen zusätzlich bestehen.
4. **5.4 Betriebspflege:** Version/Deployment und Recovery-Anleitung festhalten, Zertifikatsablauf
   sichtbar machen, CG-Ausnahme prüfen. Dependabot-PRs nach aktueller CI bewerten; SBOM und
   Image-Scan ergänzen. Beim Mailbericht nur einen fehlenden Integrationsnachweis nachholen,
   falls der Versand im bestätigten Livebetrieb noch nicht genutzt wird.

**Abnahme:** Zugriff ausschließlich über den vorgesehenen HTTPS-Weg; Secure-Cookie und
SSO-Zulassung/-Ablehnung einschließlich Gruppenentzug getestet; Notzugang erprobt. Eine externe
Sicherung wird auf einem getrennten Testziel einschließlich Konfiguration und benötigter
Secret-Bereitstellung erfolgreich wiederhergestellt; RPO/RTO und Backup-Alarme sind nachgewiesen.

Bezug: KI-1, KI-4, KI-6, KI-11, KI-13, KI-24, KI-28 bis KI-30. Das bekannte Initialpasswort
bleibt eine bewusste Entscheidung (ADR 0007); Änderungen daran brauchen eine neue Entscheidung.

## 5. Phase 6 – Verlässliche Datenbasis und Bedienung

**Ergebnis:** Gemeinsame Grundlage für Lizenz- und IAM-Funktionen ohne Verlust vorhandener Daten.

1. **6.1 Fachmodell und Migration:** stabile interne Konto- und Quellenreferenzen, Herkunft und
   Zuordnungsgrund; bestehende Identitäten, Ausnahmen und Entscheidungen erhalten. Verwendeten
   Entra-Connect-Quellanker prüfen; `objectGUID` ist nicht in jeder Umgebung der konfigurierte
   Anker. Unklare Zuordnungen bleiben prüfbar und werden nicht erzwungen.
2. **6.2 Historie und Qualität:** vergleichbare Bestände je erfolgreichem/teilweisem Lauf,
   Änderungen und Kennzahlen über Zeit. Je Quelle und Merkmal Vollständigkeit und Alter zeigen.
   Vergleiche über unvollständige oder unterschiedlich alte Bestände kennzeichnen; ein fehlender
   Datensatz belegt weder Austritt noch Lizenzentzug. Aufbewahrung für Rohdaten, Historie,
   Entscheidungen und Audit-Daten samt Bereinigung festlegen.
3. **6.3 Mandantenverwaltung:** Einstellungen, Namensmuster, Berichtsempfänger und Verbindungstests
   in der Oberfläche; Secret-Namen bleiben Referenzen. Vor Umsetzung per ADR entscheiden,
   ob YAML oder Datenbank führend ist, einschließlich Import, Export und Konfliktverhalten.
   Der heutige Startabgleich aus YAML darf Oberflächenänderungen nicht überschreiben.
4. **6.4 Bedienung:** Fortschritt und Ergebnis von Abrufen, Kontoverlauf, verständliche
   Qualitätswarnungen; Sammelentscheidung „behalten“ mit Vorschau, Umfang und Audit-Nachweis.

**Abnahme:** Migration einer anonymisierten Altstruktur erhält Referenzen und Entscheidungen;
mindestens zwei definierte Vergleichsläufe ergeben reproduzierbare Änderungen. Teilabruf,
fehlende Quelle, Wiederauftauchen und Rohdatenbereinigung verfälschen keine Historie oder
Optimierungskennzahl. Konfiguration übersteht Neustart und Wiederherstellung; fremde Mandanten
bleiben bei Anzeigen, Änderungen und Exporten getrennt.

Bezug: KI-18 bis KI-21, KI-23, KI-25, KI-26. Personen-/Verantwortlichenbezug folgt in Phase 8.

## 6. Phase 7 – Lizenzmanagement und Kosten

**Ergebnis:** Erstes eigenständig nutzbares Lizenzmanagement mit nachvollziehbaren Zahlen.

1. **7.1 Lizenzcockpit:** je Mandant/SKU vorhandene und verbrauchte Einheiten, rechnerisch freie
   Einheiten, Produktstatus, Zuweisungen je Konto und Zuweisungsfehler. Direkte und gruppenbasierte
   Zuweisung mit verantwortlicher Gruppe erhalten; derselbe Benutzer/SKU zählt nur einmal.
   Unterschiede zwischen Bestands- und Kontenzahlen erklären, statt sie rechnerisch zu verbergen.
2. **7.2 Preise und Verträge:** gepflegte Eingabe bzw. validierter CSV-Import für Einkaufspreise,
   Währung, Abrechnungszeitraum, Menge, Laufzeit, Verlängerung und Kündigungsfrist je Vertrag/SKU.
   Preisänderungen versionieren, Importvorschau und Fehlerbericht anbieten. Mehrere Verträge
   derselben SKU, nicht zugeordnete Bestände und fehlende Preise ausdrücklich darstellen.
   Pflege zunächst nur für die bestehende Admin-Rolle; feinere fachliche Rollen folgen in Phase 9.
3. **7.3 Optimierungskandidaten:** inaktive/deaktivierte lizenzierte Konten, freie Bestände,
   Zuweisungsfehler und mögliche Produktüberschneidungen mit Regel, Datenstand und Ausnahmebezug.
   Ein Kandidat enthält den geeigneten Bearbeitungsweg (direkt, Gruppe, dynamische Regel).
   Ressourcen-, Archivierungs-, Aufbewahrungs- und Serviceabhängigkeiten vor Empfehlungen prüfen.
4. **7.4 Berichte:** Kosten- und Bestandsverlauf, Vertragsfristen, nachvollziehbare Kandidatenliste,
   CSV/XLSX und Kennzahlen im bestehenden Mailbericht. Produktbezogene Nutzungsberichte nur als
   zusätzliche Quelle, wenn API, Rechte, Lizenzvoraussetzungen und Datenqualität geprüft sind.

Kostenmodell verbindlich unterscheiden:

- **Gebundene Vertragskosten:** vereinbarte Menge × gültiger Preis für den betrachteten Zeitraum;
  Graph-Bestände allein liefern keine verlässlichen Einkaufspreise oder Kündigungsbedingungen.
- **Kalkulatorisches Potenzial:** Kandidatenmenge × zugeordneter Preis, mit Annahmen und Zeithorizont.
  Unbekannte Preise sind unbekannt; Währungen werden ohne belegten Kurs nicht addiert.
- **Freigewordene Kapazität:** bestätigte Entfernung einer Zuweisung, für andere Konten nutzbar.
- **Realisierte Einsparung:** belegte Vertrags-/Rechnungsreduzierung gegenüber dokumentierter
  Ausgangsbasis. Reine Entlizenzierung reduziert eine feste Vertragsmenge nicht automatisch.

**Abnahme:** definierte Beispiele für Direkt-/Gruppenzuweisung, Mehrfachverträge, Preiswechsel,
Gratis-SKU, fehlenden Preis und Vertragsbindung stimmen mit unabhängiger Handrechnung überein.
Cockpit und Export verwenden denselben Datenstand. Keine Doppelzählung, keine Kostenaussage ohne
Herkunft; Kontoanmeldung und Überschneidung allein begründen keine sichere Entzugsempfehlung.
Niklas nimmt die Zahlen beider Pilotmandanten und den Bearbeitungsablauf ab.

## 7. Phase 8 – IAM-Transparenz und Risikobewertung

**Ergebnis:** Nachvollziehbares Bild von Identitäten, Berechtigungen und Verantwortlichkeiten.

1. **8.1 Personen und Konten:** Verantwortliche, Abteilung/Kostenstelle soweit verfügbar, Zuordnung
   mehrerer Benutzer-/Admin-Konten zu einer Person. Herkunft und manuelle Bestätigung dokumentieren;
   keine automatische Personenverschmelzung allein aufgrund ähnlicher Namen oder E-Mail-Adressen.
   Dienst-/Ressourcenkonten und technische Identitäten erhalten eigene Verantwortliche.
2. **8.2 Gruppen und Rechte:** Entra-/AD-Gruppen, direkte und vererbte Mitgliedschaften mit Pfad,
   Gruppenverantwortliche sowie Entra-Verzeichnisrollen. Privilegierte, dynamische und
   synchronisierte Gruppen kenntlich machen. Aktivierte und nur berechtigbare Rollen (PIM)
   unterscheiden, soweit die Quelle dies liefert. Eine Gruppenliste bildet nicht sämtliche
   effektiven Datei-, App- oder Cloudberechtigungen ab; die Abdeckung bleibt sichtbar.
3. **8.3 Sicherheitsbefunde:** verwaiste Konten, privilegierte inaktive Konten, externe Zugänge,
   fehlende Verantwortliche und ablaufende Ausnahmen. MFA-Registrierung und wirksame MFA-Anforderung
   unterscheiden; bei fehlenden Daten oder Rechten lautet das Ergebnis „unbekannt“.
4. **8.4 Technische Identitäten:** App-Registrierungen/Service Principals und verfügbare
   Berechtigungs-/Zertifikatsmetadaten erfassen; Eigentümer, hohe Rechte und Ablauffristen anzeigen.
   Private Schlüssel und Secret-Werte werden nicht eingelesen.

Vor jedem Connector-Ausbau benötigte API-Felder, kleinste Leserechte, Consent, Datenumfang und
Lizenzvoraussetzungen prüfen und dokumentieren. Neue Sammler müssen unabhängig scheitern können;
fehlende Rechte dürfen die bisherigen Kontoabrufe nicht unbrauchbar machen.

**Abnahme:** synthetische Szenarien für mehrere Konten je Person, gleiche Namen verschiedener
Personen, verschachtelte Gruppen (einschließlich Zyklen), privilegierte und unvollständige Quellen
zeigen nachvollziehbare Ergebnisse. Jeder Befund ist auf eine Quelle und Regel zurückführbar;
ein Betreiber kann seine Abdeckung und Unsicherheit erkennen.

## 8. Phase 9 – Governance, Prüfungen und Lebenszyklus

**Ergebnis:** Aus Befunden werden zugewiesene, fristgerechte und belegbare Entscheidungen.

1. **9.1 Rollen im Tool:** fachliche Rollen für Lesen, Bearbeiten, Prüfen/Genehmigen und
   Administration; Berechtigungen pro Route und Export. Zunächst weiterhin nur Softchem-IT.
   Verantwortlicher, Antragsteller und Genehmiger müssen unterscheidbar sein.
2. **9.2 Maßnahmen und Anträge:** aus einem Befund einen Vorgang mit Verantwortlichem, Frist,
   Begründung, Status und Wiedervorlage erzeugen. Wiederholte Abrufe erzeugen keine doppelten
   offenen Vorgänge; Antragsteller genehmigen ihre eigene Maßnahme nicht selbst.
3. **9.3 Zugriffsprüfungen:** wiederkehrende Prüfung von Gruppen, Rollen, Gästen, technischen
   Identitäten und Lizenzen; festgehaltener Prüfumfang, Entscheidung, Ausnahme, Ablauf und Eskalation.
4. **9.4 Eintritt/Wechsel/Austritt:** manuell ausgelöste Vorgänge mit Soll-Zugriffen und Checklisten
   für Konten, Rechte und Lizenzen. HR-Anbindung bleibt eine getrennte spätere Entscheidung;
   bis dahin wird kein Austritt aus Inaktivität oder fehlenden Quelldaten abgeleitet.
5. **9.5 Umsetzungsnachweis:** externe Durchführung dokumentieren und beim nächsten passenden
   Abruf kontrollieren. Erledigt-Meldung und verifizierte Änderung getrennt führen; Kostenersparnis
   zusätzlich mit Vertragsdaten belegen. Berichte und Entscheidungsprotokolle exportieren.

**Abnahme:** je ein vollständiger Lizenz-, Zugriffs- und Austrittsvorgang mit Antrag, Ablehnung/
Genehmigung, Frist, externer Umsetzung und Verifikation. Unbefugte und Selbstgenehmigungen werden
abgelehnt; neue Befunde oder geänderte Quelldaten machen alte Freigaben sichtbar ungültig.
Prüfentscheidungen bleiben während ihrer festgelegten Aufbewahrung rekonstruierbar.

## 9. Phase 10 – Option: kontrollierte Ausführung

**Ergebnis dieser Planungsstufe:** Entscheidung, ob das Tool künftig freigegebene Maßnahmen in
Quellsystemen ausführen soll. Der Auftrag für die Roadmap erteilt diese Freigabe nicht.

Vor einem Umsetzungspilot: ausdrückliche fachliche Freigabe durch Niklas, ADR zur neuen
Produktgrenze, angepasste Sicherheits-/Mitwirkungsregeln und separat berechtigte Schreib-Connectoren.
Lesende Connectoren und ihre Zugangsdaten bleiben getrennt. Erste mögliche Maßnahme ist eine
direkte Lizenzzuweisung/-entfernung in einem Testmandanten; Gruppenänderungen, Kontensperrung,
Provisionierung und Löschung wären jeweils eigene spätere Freigaben.

Ein Pilot braucht Vorschau mit Auswirkungen, Vier-Augen-Freigabe, Vergleich mit aktuellem
Quellzustand direkt vor Ausführung, begrenzten Umfang, Notabschaltung und vollständigen
Ausführungsnachweis. Hybride Konten werden nur im dafür führenden System geändert.
Veraltete Freigaben und bereits ausgeführte Aufträge werden nicht erneut angewandt.
Bei unklarer API-Antwort erst Zustand abgleichen, dann über Wiederholung entscheiden;
ein vorhandener Idempotenzschlüssel allein garantiert keine idempotente Fremd-API.

**Freigabe zum Produktivpilot:** in isolierten Tests sind Doppelauftrag, Timeout, Teilfehler,
Rechteentzug, zwischenzeitliche Änderung und Abbruch nachgewiesen. Jede Maßnahme besitzt eine
geprüfte Kompensation oder ist als nicht reversibel klassifiziert und gesondert ausgeschlossen
oder freigegeben. Keine unbegleitete Ausführung oder permanente Löschung im ersten Pilot.

## 10. Phase 11 – Integrationen und bedarfsgerechte Skalierung

Nach den Kernfunktionen: abgesicherte Lese-API für Inventar/Reports, ITSM-/Ticket-Anbindung,
weitere Verzeichnisse/SaaS-Anbieter und bei Bedarf HR-Anbindung. Jede Integration braucht einen
konkreten Anwendungsfall, Datenvertrag, Verantwortlichen und eigene Abnahmekriterien.

Vor mehreren Prozessen oder parallelen Schreibern: Entscheidung über Postgres, dauerhafte
Jobwarteschlange, prozessübergreifende Sperren, Wiederanlauf und getrennte Collector/Worker.
Heutige In-Memory-Sperren schützen nur innerhalb eines Prozesses. Migration und Betrieb werden
auf einem anonymisierten repräsentativen Datenbestand samt Restore und Lasttest geprüft.
Mandanten mit eigenen Kundenzugängen benötigen zusätzlich ein eigenes Rollen-/Isolationskonzept.

**Auslöser:** gemessene Datenmengen, Laufzeiten, Speicherbedarf, Schreiblasteffekte oder
Wiederanlaufanforderungen überschreiten die in Phase 6/7 vereinbarten Betriebsziele. Schwellen
und Lastprofil werden vor einer Architekturänderung festgelegt und danach überprüft.

## 11. Erfolgsmessung und Lieferregeln

| Dimension | Nachweis pro Mandant |
|---|---|
| Datenqualität | Alter/Vollständigkeit je Quelle, Anteil unbekannter Zuordnungen und Lizenzzustände |
| Lizenzmanagement | Preis-/Vertragsabdeckung, freie Kapazität, Potenzial und belegte Einsparung getrennt |
| IAM | Anteil Konten/Gruppen mit Verantwortlichem, offene privilegierte Befunde, befristete Ausnahmen |
| Governance | offene/überfällige Prüfungen, Zeit bis Entscheidung, Anteil verifizierter Umsetzungen |
| Betrieb | erfolgreiche Sicherung/Restore, gemessene RPO/RTO, Laufdauer und Fehlerquote |

Ausgangswerte je Pilotmandant bei Einführung der jeweiligen Funktion erheben. Zielwerte mit
Niklas anhand dieser Basis festlegen; unvollständige Daten nicht als Verbesserung werten.

Ein Lieferpaket umfasst UI/CLI, Fachlogik, Datenmigration, minimale Connector-Rechte, sinnvolle
Tests, Doku und Release-/Rückfallanleitung soweit betroffen. Bestehende Ausnahmen und Audit-Daten
erhalten; Aufbewahrungsregeln beachten. Refactoring und Verhalten in getrennten PRs.
CI bleibt grün, Abdeckungsuntergrenze mindestens 95 %, keine Absenkung zur Lieferung.
Jede neue Funktion wird zunächst begrenzt am Pilot abgenommen; der laufende Tageszyklus bleibt
für alle anderen Mandanten nutzbar. Änderungen werden nicht ungeprüft auf die Synology ausgerollt.

## 12. Nächste umsetzbare Pakete

1. **5.1:** Proxy-Variante und URL festlegen, HTTPS-Konfiguration mit Betriebsanleitung liefern.
2. **5.2 / 5.3:** SSO-Voraussetzungen und externes Backup-Ziel mit Niklas konkretisieren;
   getrennte Umsetzungen für Anmeldung und Wiederherstellung vorbereiten.
3. **6.1:** Datenmodell für Bestand, Historie und Quellenzuordnung mit Migrationsplan entwerfen;
   Preis-/Vertragsmodell für Phase 7 anschließen. Jeweils ADR vor Umsetzung.
4. **7.1:** nach dem Datenfundament zuerst das Lizenzcockpit liefern; anschließend Preise,
   Verträge und Optimierungskandidaten. IAM-Inventar und Governance folgen darauf.

Offene Voraussetzungen blockieren nur das abhängige Paket. Für Proxy/SSO/Backup entscheidet
Niklas die Betriebsparameter; Entwicklung, Tests, Migration und Doku gehören zum jeweiligen
Entwicklungspaket. Detailtickets entstehen pro anstehender Phase, nicht für alle späteren
Optionen im Voraus. Weitere Ideen stehen in [BACKLOG.md](BACKLOG.md).
