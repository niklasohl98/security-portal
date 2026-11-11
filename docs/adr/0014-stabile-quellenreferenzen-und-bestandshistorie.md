# 0014 – Stabile Quellenreferenzen und nachvollziehbare Bestände

- **Status:** Accepted für das additive Basisschema (2026-11-11); Folgepakete weiterhin Entwurf
- **Umsetzungsstand:** Migration `0010` ergänzt `directory_source` und `source_account`;
  Backfill, Zuordnungsbelege, Konfliktablauf und Historie noch nicht implementiert
- Ergänzt ADR 0009; ADR 0006 bleibt für die lokale Löschentscheidung verbindlich

## Kontext

`identity.id` ist bereits eine interne Referenz. `identity.key` verwendet dagegen je nach
Quellenlage eine Entra-Objekt-ID oder AD-objectGUID. `_resolve_row` in `app/core/identities.py`
stellt diesen Schlüssel beim Hinzukommen eines Partners um. Existieren zwei Zeilen, überträgt
die Funktion Ausnahmen auf die Entra-Zeile und löscht die andere. Manuelle Kontoart und
Prüfentscheidung der zweiten Zeile werden dabei nicht zusammengeführt.

Entra-/AD-Rohkonten sind Abrufstände. Nach ihrer Bereinigung werden Referenzen aus `identity`
zu `NULL`. Der Zuordnungsgrund aus `Pair.matched_by` wird bislang nur aggregiert ausgegeben,
nicht je Identität gespeichert. Eine spätere Historie darf diese wechselnden Rohdatenzeilen
nicht als dauerhafte Kontoidentität verwenden.

Die erste Merge-Stufe interpretiert `onPremisesImmutableId` als Base64 der objectGUID-Bytes.
Ein syntaktisch passender Wert belegt aber nicht, welchen Quellanker Entra Connect tatsächlich
verwendet. Die Pilotkonfiguration muss vor einer geänderten Ankerzuordnung geprüft werden.

## Entscheidung und geplante Folgepakete

1. **Interne Referenz erhalten:** Bestehende `identity.id`, Ausnahmen, manuelle Angaben und
   Prüfentscheidungen bleiben beim Upgrade unverändert. `identity` bezeichnet weiter ein
   zusammengeführtes Konto; Personen und Verantwortliche folgen erst in Phase 8.
2. **Quellen abgrenzen:** Ein Quellverzeichnis erhält eine interne, mandantengebundene Referenz.
   Entra-Tenant und AD-Verzeichnis haben getrennte Namensräume. Ein Wechsel des angeschlossenen
   Verzeichnisses darf bestehende Quellkonten nicht stillschweigend wiederverwenden.
3. **Quellkonto stabil speichern:** Ein Quellkonto wird durch Verzeichnis und externe Objekt-ID
   identifiziert. UPN, Mail und sAMAccountName sind veränderliche Beobachtungen, keine dauerhaften
   Schlüssel. Abrufstände verweisen zusätzlich auf das stabile Quellkonto.
4. **Zuordnung belegen:** Verknüpfungen zwischen Quellkonto und Identität speichern Herkunft,
   Regelversion, Eingabeläufe und Gültigkeit. Altdaten ohne beweisbaren Zuordnungsgrund erhalten
   `legacy_unknown`; die Migration erfindet weder eine Regel noch einen historischen Zeitpunkt.
5. **Konflikte erhalten:** Ein Abruf ersetzt keine bereits belegte Zuordnung allein wegen
   Namensgleichheit. Zwei bestehende Identitäten werden nicht automatisch gelöscht oder
   verschmolzen. Ein Konflikt bleibt prüfbar, ohne Ausnahmen oder manuelle Angaben zu übertragen.
6. **Quellanker explizit behandeln:** Der bisherige Algorithmus bleibt bis zur Umstellung als
   Altverfahren dokumentiert. Ein neues Verfahren verwendet einen GUID-Anker nur mit geprüfter
   Konfiguration; ein unbekannter Anker ist kein negativer Matchbeleg. Ein bestätigter Widerspruch
   darf nicht durch einen schwächeren Namensvergleich übergangen werden.
7. **Bestände von Bewertungen trennen:** Künftige Bestände binden konkrete Quellläufe und
   Qualitätsmerkmale. Die zeitabhängige Inaktivitätsbewertung bleibt ein eigener Vorgang.
   Fehlender Datensatz, unvollständiger Bestand und bestätigte Abwesenheit sind verschiedene
   Aussagen. Eine Kontoanmeldung ist kein Beleg für die Nutzung einer bestimmten SKU.

Tabellen, Übergang und Prüffälle stehen im
[Migrationsplan](../phase-6-data-foundation.md). Dieser Entwurf beschreibt das Ziel; die aktuelle
[Datenmodellreferenz](../data-model.md) bleibt die Quelle für das implementierte Schema.

Das erste Lieferpaket implementiert ausschließlich den Quellen-Namensraum und stabile
Quellkontoschlüssel samt mandantengebundenen Fremdschlüsseln. IDs werden als nichtleere,
kleingeschriebene Werte ohne Rand-Leerzeichen gespeichert; die Datenbank weist nichtkanonische
Werte zurück. Es gibt keinen automatischen Import aus der heutigen Mandantenkonfiguration und
keinen angenommenen AD-Verzeichnisanker. Nachweis-/Ankerfelder, Beobachtungsläufe und
Zuordnungslinks folgen mit den jeweiligen Schreibpfaden, nicht als vorgetäuschte Provenienz.

## Alternativen

- **Mehr Felder nur an `identity`:** kleiner Einstieg, trennt aber Quellverzeichnis, Abrufstand
  und wechselnde Zuordnung nicht; für eine belastbare Historie unzureichend.
- **Alle Rohdaten unbegrenzt behalten:** erleichtert Rückblicke, stabilisiert keine Referenzen
  und erweitert die personenbezogene Aufbewahrung ohne festgelegten Zweck.
- **Vollständiges Personenmodell oder Event Sourcing sofort:** vergrößert den Umfang vor dem
  Lizenzcockpit. Der bestehende Stack mit additiven Tabellen genügt zunächst.
- **Alte Identitäten neu erzeugen:** vereinfacht das neue Schema, gefährdet aber Referenzen,
  Ausnahmen und Entscheidungen; ausgeschlossen.

## Konsequenzen und offene Grenzen

Die Einführung erfolgt additiv mit überprüfbarem Backfill und anschließendem Reader-Wechsel.
Bis zur vollständigen Umstellung bleibt `identity.key` aus Kompatibilitätsgründen erhalten.
Eine manuelle Konfliktauflösung mit Vorschau und Audit muss vor einer produktiven Änderung
bisheriger Zusammenführungen geliefert werden; ein neuer Konfliktstatus allein genügt nicht.

Quellanker-Nachweise für CG/CVH sowie die Aufbewahrung neuer personenbezogener Historie sind vor
deren jeweiliger Produktivfreigabe festzulegen. Das blockiert nicht den additiven Schemaentwurf
oder synthetische Migrationstests. Preise und Verträge erhalten vor Phase 7.2 eine eigene ADR;
dieser Entwurf schafft lediglich die Verknüpfung zum nachgewiesenen Lizenzbestand.

Neue Leserechte, externe Infrastruktur und Schreibzugriffe auf Kundenverzeichnisse sind für
dieses Paket nicht vorgesehen. Phase 5 bleibt unabhängig davon bis zu ihren Live-Nachweisen offen.
