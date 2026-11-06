# 0006 – Verschwundene Identitäten prüfen statt automatisch löschen

- **Status:** Accepted (2026-11-06)

## Kontext
`refresh_identities` führt die Konten der jüngsten Läufe zusammen. Eine Identität (Zeile in
`identity`) ohne Konto im aktuellen Stand war zunächst „löschen samt Ausnahmen“ (KI-20). Das ist
riskant: Ein `partial`-Lauf (ein DC fällt aus, Graph liefert unvollständig) kann Konten
auslassen, und mit ihnen verschwänden manuell gepflegte Ausnahmen (Break-Glass, Dienstkonten) und
die manuelle Kontoart. Rückgängig machen lässt sich das nicht.

## Entscheidung
Niklas hat am 6. Nov. 2026 entschieden: Verschwundene Identitäten werden **nie automatisch
gelöscht**. Eine kleine Prüfliste in der Oberfläche (`/review`) lässt einen Menschen je Eintrag
entscheiden:

- **Behalten:** Die Zeile bleibt samt Ausnahmen. Sie erscheint unter „Behalten“ und nicht mehr
  in der offenen Liste; „Wieder prüfen“ nimmt die Entscheidung zurück.
- **Löschen:** Die Zeile und ihre Ausnahmen werden entfernt (nicht rückgängig). Vorher kommt eine
  Bestätigungsseite.
- Taucht das Konto später wieder auf, löscht `refresh_identities` Markierung und Entscheidung
  und nimmt die Zeile wieder in die normale Auswertung (Ausnahmen bleiben).

Technisch: `identity.missing_since`, `review_decision` (`keep` oder leer), `reviewed_at`
(Migration `0004`). Verschwundene Zeilen bekommen den Status `missing`, sind nie geflaggt und
tragen keine Empfehlung. Jede Entscheidung steht im Audit-Log (`identity.keep|delete|reopen`,
Akteur, `identity_id`, Zahl der Ausnahmen; keine Namen).

Die Entscheidung betrifft nur Zeilen dieses Tools. Entra und AD werden nie verändert.

## Alternativen
- **Sofort löschen (bisher):** verliert Ausnahmen bei unvollständigen Läufen; verworfen.
- **Automatisch nach Frist löschen (z. B. 14 Tage):** gemildert, aber ein langer DC-Ausfall oder
  ein dauerhaft unvollständiger Abruf beseitigt trotzdem Ausnahmen ohne Zutun; verworfen.
- **Nie löschen, nur ausblenden:** Die Tabelle wüchse mit Karteileichen; verworfen.

## Konsequenzen
- Die Liste muss gepflegt werden: Wer sie ignoriert, behält verschwundene Einträge dauerhaft
  (sie stören die Auswertung nicht, zählen aber nicht in „Konten gesamt“).
- Die Oberfläche hat jetzt Schreibaktionen, aber noch keine Anmeldung (KI-1, Phase 5): Der
  Akteur im Audit-Log ist bis dahin „lokal (ohne Anmeldung)“. Schutz davor: Port nur an
  `127.0.0.1`, Same-Origin-Prüfung gegen CSRF, kein JavaScript, strenge CSP.
- Sammelaktionen („alle behalten“) gibt es bewusst noch nicht (Backlog).
