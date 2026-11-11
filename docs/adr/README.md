# Architekturentscheidungen

ADRs halten Entscheidungen fest, die mehrere Komponenten, den Betrieb oder die Sicherheitsgrenze
betreffen. Der Status steht am Anfang jeder Datei. Eine verworfene Entscheidung bleibt erhalten und
wird durch eine neue ADR ersetzt.

| ADR | Entscheidung |
|---|---|
| [0001](0001-stack-und-architektur.md) | Stack und Grundarchitektur |
| [0002](0002-ad-direkt-per-ldaps.md) | Active Directory direkt per LDAPS lesen |
| [0003](0003-zertifikats-login-und-docker-secrets.md) | Zertifikatsanmeldung und Docker-Secrets |
| [0004](0004-betrieb-auf-synology-und-zielgruppe.md) | Betrieb auf Synology und Zielgruppe |
| [0005](0005-befristete-ausnahme-fuer-schwache-dc-schluessel.md) | Befristete Ausnahme für schwache DC-Schlüssel |
| [0006](0006-verschwundene-identitaeten-pruefen-statt-loeschen.md) | Verschwundene Identitäten prüfen |
| [0007](0007-lokale-anmeldung.md) | Lokale Anmeldung und Notzugang |
| [0008](0008-mail-bericht-per-exchange-online.md) | Mail-Bericht über Exchange Online |
| [0009](0009-iam-und-lizenzmanagement.md) | Ausbau zu IAM und Lizenzmanagement |
| [0010](0010-https-auf-der-synology.md) | HTTPS über DSM |
| [0011](0011-entra-sso-und-lokaler-notzugang.md) | Entra-SSO und lokaler Notzugang |
| [0012](0012-externe-sicherung-mit-restic.md) | Externe Sicherung mit Restic |
| [0013](0013-betriebspflege-und-sicherheitsbuild.md) | Betriebspflege und geprüfter Sicherheitsbuild |
| [0014](0014-stabile-quellenreferenzen-und-bestandshistorie.md) | Stabile Quellenreferenzen; Basisschema angenommen, Folgepakete geplant |
