# Phase 6.1: Datenfundament und Migrationsplan

Stand: 11. November 2026. **Basisschema implementiert; Folgepakete geplant.** Grundlage ist
[ADR 0014](adr/0014-stabile-quellenreferenzen-und-bestandshistorie.md).
Der Plan konkretisiert Roadmap 6.1 und die Schnittstellen zu 6.2 und 7; er erklärt diese
späteren Pakete nicht als geliefert. Das aktuelle Schema steht in [data-model.md](data-model.md).

Migration `0010` liefert zunächst nur `directory_source` und `source_account` mit Quellenart,
externer Referenz, Anlagezeit sowie Aktivstatus des Verzeichnisses. Eindeutigkeit und
Mandantentrennung sind auf Datenbankebene geprüft. Die Migration erzeugt keine fachlichen
Datensätze und ändert keine bestehenden Tabellen. Beobachtungsläufe, Anker-/Nachweisfelder,
Rohdatenverweise und Zuordnungen werden erst mit den Folgepaketen ergänzt.
Neue erfolgreiche und teilweise Entra-Abrufe erfassen inzwischen die Verzeichnis-ID und gültige
Konto-IDs; historische Läufe und AD-Konten werden nicht nachträglich zugeordnet.

## Abgegrenztes Lieferziel

Ein Konto behält seine interne Referenz, wenn sich UPN, Anzeigename, Quellenlage oder
Rohdatenaufbewahrung ändern. Jede Quellenzuordnung kann auf ihren Ursprung zurückgeführt werden.
Das Upgrade erhält bestehende Ausnahmen und Entscheidungen, ohne echte Verzeichnisse abzurufen.
Der bestehende SQLite-/Ein-Prozess-Betrieb bleibt erhalten.

Phase 6.1 liefert zunächst Quellenreferenzen, Zuordnungsbelege, Migration und Konfliktanzeige.
Zeitreihen und Vergleichsoberfläche folgen in 6.2, Mandantenpflege in 6.3 und das Lizenzcockpit
in 7.1. Die folgende Bestandsskizze verhindert, dass 6.1 deren spätere Referenzen verbaut.

## Gegenwärtige Datenflüsse und Grenzen

| Codepfad | Heute beobachtetes Verhalten | Anforderung an den Übergang |
|---|---|---|
| `core/identities.py::_resolve_row` | Entra-ID hat Vorrang; Schlüsselwechsel oder Zusammenführen mit Löschen einer Zeile | interne IDs erhalten; Konflikte nicht durch Löschen auflösen |
| `core/merge.py` | Immutable-ID, dann sAMAccountName, dann UPN; Grund nur im Merge-Ergebnis | Quellanker und Regelversion dokumentieren; Beleg je Verknüpfung |
| `core/retention.py` | alte Rohkonten bereinigen; jüngsten brauchbaren Lauf schützen | dauerhafte Referenzen unabhängig von Rohdaten halten |
| `core/review.py` | lokale Entscheidung behalten/löschen/wieder prüfen | ADR 0006 erhalten; Wiederauftauchen und Konfliktauflösung unterscheiden |
| `db/models.py::Identity` | Fremdschlüssel auf Abrufzeilen mit `ON DELETE SET NULL` | Referenzverlust als unbekannt behandeln, nicht rückwirkend ergänzen |

Diese Grenzen sind aus dem Code abgeleitet. Es wurde kein Verlust produktiver Daten behauptet
oder geprüft. Die bestehenden Regressionstests belegen insbesondere den Erhalt von Ausnahmen
und einer manuellen Kontoart beim Hinzukommen eines Entra-Partners.

## Vorgeschlagenes Schema für 6.1

Alle neuen Kundentabellen tragen `tenant_id`. Beziehungen verwenden zusammengesetzte
Fremdschlüssel einschließlich `tenant_id`, damit auch die Datenbank mandantenfremde Verweise
ablehnt. Dazu sind entsprechende eindeutige Elternschlüssel nötig. SQL-Abfragen filtern
zusätzlich nach Mandant. Zeitwerte bleiben UTC-aware.

| Entität | Schlüssel und Inhalt | Integritätsregel |
|---|---|---|
| `directory_source` | interne ID, Mandant, Art `entra/ad`, Verzeichnisreferenz, Aktivstatus, konfigurierte Ankerart und deren Nachweiszeitpunkt | gleicher Entra-Tenant bzw. gleiches bestätigtes AD-Verzeichnis je Mandant nur einmal; Umbenennung ist kein Verzeichniswechsel |
| `source_account` | interne ID, Mandant, Verzeichnis, externe Objekt-ID, erster/letzter beobachteter Lauf | eindeutig je `(tenant_id, directory_source_id, external_id)`; Quelle und externe ID nach Anlage unveränderlich |
| `identity_source_link` | interne ID, Mandant, Identität, Quellkonto, Beginn/Ende der Zuordnung, Herkunft, Regelversion, Belegläufe | höchstens eine aktive Zuordnung je Quellkonto; höchstens ein aktives Konto je Identität und Quellverzeichnis |
| `reconciliation_case` | interne ID, Mandant, beteiligte Identitäten/Quellkonten, Konfliktcode, Belegläufe, Status, Entscheidung | wiederholte Auswertung derselben offenen Konstellation erzeugt keinen zweiten Fall |

Die bisherige `identity.id` bleibt der interne Schlüssel; eine zusätzliche Konto-UUID ist für
den aktuellen Ein-Instanz-Betrieb nicht erforderlich. `identity.key` bleibt während des
Übergangs unverändert lesbar, wird aber nicht mehr zur quellübergreifenden Identifikation genutzt.
Rohkonten erhalten einen zusätzlichen Verweis auf `source_account`; bestehende Rohdatenfelder
werden nicht umgedeutet. Bereits gespeicherte Läufe lassen sich nicht immer zuverlässig einem
früheren Verzeichnis zuordnen: fehlender Nachweis bleibt ausdrücklich ungeklärt.

Für aktive Links werden partielle eindeutige Indizes mit `valid_to IS NULL` verwendet.
Die aktive Zuordnung wird in einer Transaktion beendet/angelegt. `valid_from` bezeichnet bei
der Migration nur den Übernahmezeitpunkt; es ist kein erfundenes Datum der ursprünglichen
Kontoanlage. Historische Linkzeilen behalten ihre IDs und ihren Beleg.

Bei AD muss die Verzeichnisreferenz einen Domänenwechsel von einer bloßen DNS-Umbenennung
unterscheiden. Vor einer automatischen Übernahme ist deshalb eine bestätigte Verzeichniskennung
nötig; aus einem DNS-Namen allein wird keine unveränderliche Identität behauptet. Bei Entra
dient die konfigurierte Verzeichnis-ID als Namensraum. Änderungen der App-Registrierung oder
eines Secret-Namens erzeugen keine neue Quelle.

## Zuordnung und Konfliktverhalten

1. Ein bereits bekanntes externes Objekt wird zuerst im selben Quellen-Namensraum aufgelöst.
   Eine umbenannte Anmeldung erzeugt weder ein neues Konto noch einen Personenwechsel.
2. Bestehende, widerspruchsfreie Links bleiben erhalten, auch wenn eine Quelle in einem
   Teilabruf fehlt. Ein Quellkonto erhält bei fehlenden Rohdaten keinen erfundenen Zustand.
3. Neue Partnerschaften erhalten Methode und Regelversion. Für den GUID-Vergleich müssen
   Ankerkonfiguration und gelesener Wert zusammenpassen. Weitere Ankerarten benötigen eine
   gesonderte Connector-Erweiterung mit Tests und geprüften API-/LDAP-Feldern.
4. Ein unbekannter Anker lässt den Ankervergleich offen. Namensvergleiche dürfen einen Kandidaten
   liefern, aber keine zwei vorhandenen Identitäten automatisch vereinen. Widersprüchliche
   bestätigte Anker sperren einen automatischen Rückfall auf Namen.
5. Mehrdeutigkeit blockiert die automatische Verbindung der beteiligten Konten auch in
   schwächeren Stufen. Das neue Verhalten bekommt eine neue Regelversion; bestehende Links
   werden nicht stillschweigend nach einer neuen Regel umgeschrieben.
6. Treffen zwei bestehende Identitäten aufeinander, bleiben beide samt Ausnahmen, Kontoart
   und Prüfentscheidung erhalten. Die Konfliktansicht zeigt Folgen und Widersprüche; eine
   fachliche Zusammenführung benötigt eine ausdrückliche Entscheidung mit Audit-Nachweis.
   Ausnahmen werden weder automatisch vereinigt noch auf ein anderes Konto erweitert.
7. Wiederauftauchen desselben Quellobjekts folgt weiterhin dem Ablauf aus ADR 0006. Ein neues
   Objekt mit gleicher Anmeldung übernimmt keine früheren Ausnahmen oder Entscheidungen.

Audit und technische Logs enthalten nur interne IDs, Anzahlen und feste Codes. Belegwerte wie
UPN oder Immutable-ID bleiben in geschützten Bestandsdaten, nicht im Audit-Detail. Neue
Aktionscodes werden bei Implementierung im [Audit-Katalog](audit-log.md) dokumentiert.

## Anschluss für Bestand und Qualität in 6.2

Ein künftiger `inventory_snapshot` bindet einen festen Satz von Eingabeläufen, Quellkonfiguration
und Erzeugerversion. `inventory_member` bindet stabile Quellkonten, ihre damalige Zuordnung und
die benötigten Beobachtungen. Lizenzbestände und Zuweisungen werden zum selben Entra-Lauf
gespeichert. Beide Tabellen benötigen ebenfalls `tenant_id`. Ein Snapshot wird erst nach
vollständiger Transaktion sichtbar und anschließend nicht nachträglich verändert.

Idempotenz gilt für die Bestandsbildung aus identischen Eingabeläufen und derselben Version.
Eine neue zeitabhängige Inaktivitätsbewertung darf dagegen auch ohne neuen Abruf andere
Ergebnisse liefern und erhält ihren eigenen Bewertungszeitpunkt und ihre Regelversion.

| Qualitätsaussage | Bedeutung für Vergleiche |
|---|---|
| Quelle vollständig und vergleichbar | Hinzu-/Abgänge dieser Quelle können ausgewiesen werden |
| Quelle teilweise oder Vollständigkeit unbekannt | beobachtete Werte nutzbar; Abwesenheit bleibt unbestätigt |
| Anmeldewerte fehlen, Konten vollständig | Konto-/Lizenzbestand und Anmeldebewertung getrennt beurteilen |
| Quelle fehlt oder stammt aus älterem Lauf | Alter je Quelle anzeigen; keinen synchronen Gesamtbestand vortäuschen |
| Quellverzeichnis oder Erfassungskriterien geändert | Vergleich unterbrechen bzw. ausdrücklich eingeschränkt ausweisen |

`success/partial` allein genügt nicht als Qualitätsmodell. Ein fehlendes Anmeldefeld ist etwas
anderes als ein unvollständiges Paging oder fehlende AD-Stammdaten. Warncodes werden deshalb
in explizite Merkmalsabdeckung übersetzt; unbekannte Warncodes werden konservativ behandelt.
Die späteren Kennzahlen zählen Quellkonto, zusammengeführtes Konto und Person nicht austauschbar.

Rohdatenbereinigung bleibt zunächst bei der bestehenden Regel. Eine separate Historie darf
nicht unbemerkt zur unbegrenzten Kopie sämtlicher Rohdaten werden. Vor ihrer Produktivierung
werden Feldauswahl, Fristen, Bereinigung, Verhalten bei lokaler Identitätslöschung und
Sicherungsaufbewahrung gemeinsam festgelegt. Bereits entfernte Rohdaten werden nicht rekonstruiert.
Eine Löschung nach ADR 0006 darf nicht durch einen Backfill rückgängig gemacht werden.

## Anschluss an Lizenzmanagement

Phase 7.1 verbindet Bestand und Zuweisungen über Mandant, Entra-Verzeichnis, SKU-ID und
konkreten Lauf/Snapshot. Produktnamen bleiben eine Anzeigezuordnung; sie sind kein Vertragskey.
Eine künftige Vertragsposition verweist auf Mandant/SKU und gegebenenfalls Quelle, mit eigener
Menge, Währung und Preisgültigkeit. Mehrere Vertragspositionen pro SKU bleiben möglich.

Graph-Verbrauch ist kein Einkaufspreis. Fehlender Vertrag oder Preis bleibt unbekannt;
Währungen werden ohne belegte Umrechnung nicht addiert. Das Preis-/Vertragsmodell einschließlich
Versionierung und Import erhält vor 7.2 eine eigene ADR.

## Einführung und Rückfall

| Schritt | Umsetzung | Nachweis vor dem nächsten Schritt |
|---|---|---|
| A: additive Migration | `0010`: Verzeichnis-/Quellkontotabellen implementiert; Rohkontenverweise folgen mit Integration | Upgrade von 0009, wiederholter Start und Rückmigration geprüft; Altzeilen unverändert, Schemaabgleich und Mandantentrennung getestet |
| B: Backfill | nur belegte Quellen/Objekte zuordnen; `legacy_unknown` für unbewiesene Methode; ungeklärte Fälle separat zählen | zweiter Lauf ohne Duplikate; fehlende Rohdaten und Namensraumkonflikte verlieren keine Altzeile |
| C: Schreiben und Vergleich | neue Abrufe mit Quellenreferenz; alte Auswertung zunächst parallel vergleichen | deterministische Zuordnung, Transaktionsabbruch ohne halbe Links, Mandantentrennung |
| D: Leser umstellen | Auswertung, Kontodetail und Konfliktansicht nutzen neue Referenzen; Grund/Datenstand anzeigen | Migration plus Wiederauftauchen/Umbenennung/Teilabruf/Bereinigung end-to-end geprüft |
| E: Bestandshistorie | separates Paket 6.2 nach festgelegter Aufbewahrung | reproduzierbare Vergleiche zweier Bestände und geprüfte Bereinigung |

Migrationen erhalten die zum Implementierungszeitpunkt nächste freie Revision; der Entwurf
reserviert keine Nummer. Vor dem Upgrade wird eine geprüfte SQLite-Sicherung einschließlich
Konfiguration gemäß [Betriebsanleitung](operations.md#update-und-rückfall) erstellt. Der Backfill
läuft ohne Graph-/LDAP-Aufruf transaktional und wiederholbar. Bestehende Datensätze mit
fehlender oder widersprüchlicher Herkunft werden erhalten und ausgewiesen, statt geraten.

Ein alter Binärstand wird nicht ungeprüft gegen das neue Schema gestartet. Rückfall erfolgt
mit gestoppter Anwendung über die geprüfte Sicherung und den dazugehörigen vorherigen
Anwendungsstand. Änderungen nach der Sicherung müssen vorher separat gesichert werden;
ein Restore verwirft sie sonst. Ein Schema-Downgrade mit Verlust der neuen Zuordnungsbelege ist
kein Standard-Rückfallweg. Laufende Dienste bleiben bei einem Prozess/Worker.

## Verbindliche Prüffälle für die Implementierung

| Fall mit synthetischen Daten | Erwartung |
|---|---|
| AD-Konto erhält Entra-Partner | vorhandene interne Konto-ID, Ausnahme-ID und manuelle Kontoart bleiben erhalten |
| zwei vorhandene Identitäten mit unterschiedlichen Entscheidungen | beide bleiben unverändert; ein offener Konflikt, keine doppelte Fallanlage |
| gleiche externe GUID in AD und Entra ohne Zuordnungsbeleg | getrennte Quellkonten; keine Verbindung allein durch den GUID-Text |
| gleiche externe IDs in zwei Mandanten | getrennte Referenzen; fremde Verweise werden von der DB abgelehnt |
| UPN/sAMAccountName ändert sich | Objekt und Link behalten ihre ID; neue Beobachtung enthält die Änderung |
| neuer Benutzer übernimmt eine frühere Anmeldung | neues Quellobjekt erhält keine Ausnahme des Vorgängers |
| unbekannter oder widersprüchlicher Quellanker | kein behaupteter GUID-Beleg; Widerspruch verhindert schwächeren Automatch |
| mehrdeutiger starker, eindeutiger schwacher Schlüssel | Konflikt bleibt offen; kein erzwungenes Paar |
| Rohdaten vor Upgrade bereits bereinigt | Identität und Entscheidungen erhalten; Herkunft ungeklärt, kein erfundener Backfill |
| Teilabruf oder fehlende Quelle, danach Wiederauftauchen | Links und Ausnahmen erhalten; keine behauptete Entlizenzierung oder Einsparung |
| Backfill wiederholt oder Transaktion abgebrochen | keine Duplikate, keine halbe Zuordnung und kein Verlust von Altdaten |
| Restore und Neustart | gleiche Referenzen und Entscheidungen; alte Sitzungen bleiben ungültig |

Für 6.2 zusätzlich: zwei feste Vergleichsläufe, fehlende Merkmale, unterschiedlich alte Quellen
und Rohdatenbereinigung dürfen Historie und Kennzahlen nicht rückwirkend verändern.
Format, Lint, striktes mypy und Gesamtsuite mit Coverage bleiben Lieferbedingung für Codeänderungen.
Für das Basisschema sind Upgrade-/Rückmigration, unveränderte Altzeilen, Eindeutigkeit,
Mandantentrennung und Bestand der Referenzen nach Rohdatenbereinigung getestet. Die übrigen
Prüffälle bleiben Lieferbedingungen der Folgepakete.
