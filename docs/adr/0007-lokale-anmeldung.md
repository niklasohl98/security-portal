# 0007 – Lokale Anmeldung mit Benutzerdatenbank und erzwungenem Passwortwechsel

- **Status:** Accepted (2026-11-06)

## Kontext
Bis hierher hatte die Oberfläche keine Anmeldung (KI-1): Sie war nur an `127.0.0.1` gebunden. Sie zeigt
aber Anzeigenamen und erlaubt Entscheidungen (Ausnahmen, Löschen, Export). Der Plan sieht eine
Anmeldung per Entra ID (OIDC) vor, die an der App-Registrierung im Softchem-Mandanten hängt (Phase 5).
Niklas wollte vorab eine **lokale Benutzerdatenbank** mit einem Initialkonto `Admin` und dem
Startpasswort `Passwort123`, bei dem der Passwortwechsel **zwingend** ist.

## Entscheidung
- **Benutzer** liegen in `app_user` (nicht mandantenbezogen: sie gehören der Softchem-IT; die Regel
  „jede Tabelle hat `tenant_id`“ gilt für Kundendaten und nennt jetzt `app_user` und `app_session`
  als Ausnahme). Passwörter stehen nur als **Argon2id**-Hash (`argon2-cffi`, Voreinstellungen der
  Bibliothek) in der Datenbank, vorher Unicode-NFKC-normalisiert.
- **Initialkonto:** Ist die Tabelle leer, legt der Start `Admin` / `Passwort123` an
  (`must_change_password = true`). Es entsteht nur bei leerer Tabelle, nie erneut.
- **Zwangswechsel:** Solange der Wechsel offen ist, erreicht der Benutzer nur `/password`, `/logout`,
  `/static` und `/health`; alles andere führt (GET) dorthin oder wird (POST) abgelehnt. Das neue
  Passwort: 12 bis 128 Zeichen, nicht der Benutzername, nicht das Startpasswort, nicht das aktuelle.
  Nach dem Wechsel gilt eine neue Sitzung, alle anderen enden.
- **Sitzungen** liegen serverseitig in `app_session` (Token nur als SHA-256-Hash), 8 Stunden gültig
  (`KW_SESSION_HOURS`), Cookie `kw_session` mit `HttpOnly`, `SameSite=Lax`, `Secure` bei HTTPS
  (oder erzwungen mit `KW_COOKIE_SECURE`). Bei jeder Anmeldung entsteht ein neues Token.
- **Schutz vor Raten:** Nach 5 Fehlversuchen (`KW_LOGIN_MAX_ATTEMPTS`) ist das Konto 15 Minuten
  gesperrt (`KW_LOGIN_LOCKOUT_MINUTES`). Unbekannter Benutzer, falsches Passwort und Sperre zeigen
  denselben Text; für unbekannte Benutzer wird trotzdem gerechnet (gleiche Laufzeit).
- **CSRF:** Zusätzlich zur Same-Origin-Prüfung trägt jedes Formular ein Token je Sitzung
  (`csrf_token`), geprüft mit zeitkonstantem Vergleich.
- **Audit-Log:** Der Handelnde ist der Benutzername (statt „lokal (ohne Anmeldung)“). Anmelden,
  Abmelden, Fehlversuche (nur Benutzer-ID), Sperre und Passwortwechsel stehen im Log, nie Passwörter
  und nie Namen aus fremden Eingaben.
- **Wiederherstellung** ohne Oberfläche: `python -m app.cli user-add|user-reset|user-list`
  (Passwort wird ohne Anzeige abgefragt; der Wechsel beim ersten Login bleibt Pflicht).

## Alternativen
- **Sofort Entra-OIDC:** der Zielzustand, braucht aber die App-Registrierung und die Sicherheitsgruppe
  im Softchem-Mandanten (offen, Phase 5). Die lokale Anmeldung bleibt danach als Notzugang denkbar.
- **Zufälliges Einmalpasswort statt Startpasswort:** sicherer (nichts Bekanntes im Quelltext), wurde
  aber ausdrücklich anders gewünscht. Siehe Konsequenzen.
- **scrypt aus der Standardbibliothek:** keine neue Abhängigkeit, Argon2id gilt aber als erste Wahl
  (OWASP) und steht so im Plan.
- **Sitzung als signiertes Cookie ohne Datenbank:** kein serverseitiges Beenden möglich (Abmelden,
  Passwortwechsel, Deaktivieren); verworfen.

## Konsequenzen
- **Das Startpasswort ist öffentlich** (Quelltext, Doku). Bis zur ersten Anmeldung kann jeder, der die
  Oberfläche erreicht, als `Admin` hineinkommen und das Passwort **selbst** festlegen. Gegenmittel: Den
  Port nicht öffnen (Bindung an `127.0.0.1`) und **sofort nach dem ersten Start anmelden und ändern**.
  Das widerspricht der Regel „keine Geheimnisse im Repo“ nur, weil es kein Geheimnis ist: Es ist ein
  bekannter Startwert mit erzwungenem Wechsel (KI-28).
- Kein zweiter Faktor, keine Benutzerverwaltung in der Oberfläche, keine Sperre je IP (hinter einem
  Proxy unzuverlässig). Wer den Admin-Namen kennt, kann das Konto durch Fehlversuche für 15 Minuten
  sperren (Dienstverweigerung, akzeptiert; KI-29).
- Die Anmeldung ersetzt den Schutz durch die reine `127.0.0.1`-Bindung, nicht aber HTTPS: Ohne
  Reverse-Proxy mit TLS laufen Passwort und Cookie im Klartext durchs Netz (KI-1 bleibt dafür offen).
