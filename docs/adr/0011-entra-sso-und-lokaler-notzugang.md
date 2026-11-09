# 0011 – Entra-SSO mit vorhandener App und lokalem Notzugang

- **Status:** Accepted (2026-11-09); Live-Konfiguration/Abnahme ausstehend

## Kontext

Niklas gibt Phase 5 frei und möchte SSO über die bestehende Mail-App-Registrierung im
Softchem-Tenant abwickeln. Reale IDs/Geheimnisse werden erst beim Deployment bereitgestellt.
Der lokale Zugang muss bei einem Entra-Ausfall weiter verfügbar sein.

## Entscheidung

- OIDC Authorization Code Flow mit MSAL, PKCE, State, Nonce und browsergebundenem Einmal-Flow;
  Callback nur per POST. Eigene RS256-Validierung des ID-Tokens, da MSAL 1.39 diese Aufgabe
  nicht vollständig übernimmt. PyJWT mit Crypto-Unterstützung wird deshalb direkte Abhängigkeit
  (war bereits transitiv installiert; keine Versionsupdates mit diesem Paket).
- Eigene `KW_SSO_*`-Konfiguration, aktiv nur mit `KW_SSO_ENABLED=1` und öffentlicher HTTPS-Origin.
  Client-ID und Zertifikat dürfen dieselben wie für Mail sein; keine impliziten Fallbacks.
- Zulassung und Admin-Rolle aus getrennt konfigurierbaren Sicherheitsgruppen, abgefragt über
  lesendes Graph mit delegiertem `User.Read`. Bei Login und regelmäßig danach neu prüfen;
  Standard fünf Minuten, maximal 15. Fehler entziehen die SSO-Sitzung, ohne lokale zu blockieren.
- Eigene SSO-Identität über `(Tenant-ID, Objekt-ID)`, keine Verknüpfung über Namen/Adressen.
  Kein lokales Passwort für SSO-Konten; ihre Admin-Rolle wird ausschließlich in Entra bestimmt.
- Token-/Refresh-Caches nur im mengenmäßig/zeitlich begrenzten Prozessspeicher. Ein Neustart
  verlangt eine neue SSO-Anmeldung. Das normale Sitzungstoken liegt weiterhin nur gehasht in
  SQLite; Migration `0009` ergänzt Referenzen/Herkunft, keine Tokens.
- Lokale Anmeldung als Notzugang und mindestens ein aktiver lokaler Admin bleiben bestehen.
  Das bekannte Initialpasswort wird durch dieses Paket nicht geändert (ADR 0007).

## Konsequenzen

Eine App vereinfacht die Einrichtung, koppelt aber SSO und Mail an denselben App-/Zertifikats-
Lebenszyklus. Delegiertes User.Read darf die Exchange-RBAC-Begrenzung nicht aufheben; keine
unbegrenzte Mail.Send-Anwendungszustimmung hinzufügen. Gruppenänderungen wirken nach der
Sichtbarkeit in Graph und der nächsten fälligen Prüfung, nicht augenblicklich.
MFA wird durch Entra Conditional Access eingerichtet und separat abgenommen.
Mehrere Prozesse benötigen ein anderes Cache-/Sitzungskonzept (Roadmap Phase 11).
Einrichtung, Rückfall und Abnahme: `docs/sso-setup.md`.
