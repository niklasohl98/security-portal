# 0005 – Befristete Ausnahme für schwache RSA-Schlüssel der CG-Domänencontroller

- **Status:** Accepted (2026-11-03), befristet; Enddatum steht in `config/tenants.yaml`

## Kontext
Der Test von der Synology aus am 3. Nov. 2026 zeigt für CG: Die Domänencontroller (geprüft: zwei
von acht; die übrigen sechs sind nicht einzeln geprüft) liefern LDAPS-Zertifikate
mit **RSA-1024-Schlüssel**, ausgestellt von der CG-Root-CA (4096 Bit, gültig bis 2045). Das
Debian-Image des Containers verlangt für Zertifikate OpenSSL-Sicherheitsstufe 2 (RSA ab 2048 Bit)
und bricht mit `EE certificate key too weak` ab. Windows akzeptiert dieselben Zertifikate, daher
fiel es im Test vom PC nicht auf. CVH ist unauffällig (2048 Bit, nur die CA fehlte im Container).

Niklas hat entschieden, die CG-DC-Zertifikate **nicht** neu auszustellen: Die alte
CG-Infrastruktur wird in Wochen bis Monaten abgelöst. Bis dahin soll das Tool CG trotzdem lesen.

## Entscheidung
Eine ausdrückliche, **befristete Ausnahme je Mandant**:

- Feld `ad.accept_weak_dc_keys_until: <Datum>` in `tenants.yaml`. Bis einschließlich diesem Datum
  verwendet das Tool für die Verbindungen dieses einen Mandanten die OpenSSL-Sicherheitsstufe 1
  (RSA ab 1024 Bit) statt 2. Ohne das Feld, für alle anderen Mandanten und nach dem Datum gilt
  Stufe 2. Die Stufe wird immer ausdrücklich gesetzt, nie dem System überlassen.
- **Unverändert geprüft bleiben**: Zertifikatskette gegen die CA-Datei des Mandanten, Hostname
  gegen den DC-Namen, Gültigkeitszeitraum. Es gibt keinen Schalter, der die Prüfung abschaltet
  (`verify=False` bleibt verboten).
- Jeder Lauf mit aktiver Ausnahme trägt die Warnung `ad_weak_key_exception_active`; nach Ablauf
  des Datums `ad_weak_key_exception_expired`, und die Verbindung scheitert wieder wie zuvor.
- Tests (`tests/test_ad_tls.py`) belegen mit echten TLS-Handshakes: die Ausnahme lässt nur den
  kürzeren Schlüssel zu und lockert weder Kette noch Hostname noch Ablauf.

## Alternativen
- **DC-Zertifikate neu ausstellen (2048 Bit):** die saubere Lösung; von Niklas verworfen wegen der
  bevorstehenden Ablösung.
- **Sicherheitsstufe im Container global senken:** schwächt alle Verbindungen, auch CVH; verworfen.
- **Zertifikatsprüfung abschalten:** verboten (CLAUDE.md).
- **CG über einen Zwischenschritt anbinden (Windows-Agent, ADR 0002):** unnötiger Aufwand für eine
  auslaufende Umgebung.

## Konsequenzen
- **Restrisiko:** Wer im Netz zwischen Synology und DC vermitteln und den 1024-Bit-Schlüssel eines
  DCs ableiten kann, könnte sich als dieser DC ausgeben und das Passwort des Dienstkontos
  mitlesen (einfaches Bind über TLS). Das Konto ist nur Mitglied in „Domain Users“, das Tool
  schreibt nie ins AD, und der Aufwand für einen 1024-Bit-Angriff ist hoch. Das Risiko ist durch
  die Befristung und das interne Netz begrenzt und steht als KI-13 in `KNOWN_ISSUES.md`.
- Das Enddatum muss bewusst gesetzt werden (Vorschlag: vor Ablauf des ersten DC-Zertifikats am
  01.01.2027). Läuft die alte Umgebung länger, ist es eine neue Entscheidung, das Datum zu
  verlängern; Neuausstellung ist dann wieder die Alternative.
- Läuft ein DC-Zertifikat ab und wird mit 1024 Bit erneuert, gilt dieselbe Ausnahme weiter, solange
  das Datum nicht überschritten ist.
- Ist die CG-Umgebung abgelöst, wird das Feld aus der `tenants.yaml` entfernt; der Code bleibt
  für künftige Fälle bestehen.
