# 0002 – AD direkt per LDAPS statt Windows-Agent

- **Status:** Accepted (2026-11-02), Rückfall offen (siehe Konsequenzen)

## Kontext
Die letzte AD-Anmeldung braucht `lastLogon` von allen DCs (wird nicht repliziert).

## Entscheidung
`ldap3` über LDAPS (Port 636) aus dem Linux-Container, mit Zertifikatsprüfung. Schreibgeschütztes
Dienstkonto je Domäne (nur „Domain Users“). DC-Gesundheitscheck vor jedem Lauf.

## Alternativen
- **Windows-Agent mit PowerShell:** zusätzliche Software und Updates auf Kundenseite, zweiter
  Übertragungsweg. Nur Rückfall.
- **LDAP auf 389 ohne TLS:** abgelehnt, Klartext.
- **`lastLogonTimestamp` allein:** repliziert mit bis zu 14 Tagen Verzug, für die 30-Tage-Regel zu ungenau.

## Konsequenzen
Der Docker-Host muss alle DCs beider Kunden auf Port 636 erreichen und die CA-Zertifikate kennen.
Ist das für CVH nicht möglich, entscheidet ein neuer ADR über den Agent-Rückfall.
