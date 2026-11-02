## Was und warum

## Definition of Done

- [ ] `ruff format --check .`, `ruff check .`, `mypy`, `pytest --cov` lokal grün (Abdeckung nicht gesunken)
- [ ] Verhalten/Konfiguration/Befehl geändert → README, `docs/architecture.md`, `CHANGELOG.md` aktualisiert
- [ ] Entscheidung getroffen oder verworfen → ADR bzw. Eintrag im Backlog (mit Grund)
- [ ] Neues Risiko oder bewusste Einschränkung → `KNOWN_ISSUES.md`
- [ ] Neue Konfiguration steht in `config/tenants.example.yaml`, `.env.example` und der Doku
- [ ] Keine Secrets, Mandanten- oder Personendaten im Diff
- [ ] Keine Schreibzugriffe auf Entra oder AD
