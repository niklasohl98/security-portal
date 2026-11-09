# Restic: reproduzierbarer Sicherheitsbuild

Stand: 9. November 2026. Das offizielle aktuelle Restic-Image enthält Restic 0.19.1 mit Go 1.26.4
und im Image-Scan als verwundbar erkannten eingebetteten Abhängigkeiten. Das App-Image baut
deshalb **unveränderten Restic-Quellcode** mit aktualisiertem Compiler und gesperrten
Abhängigkeiten. Dies ist ein eigener Build, kein unverändertes offizielles Binärartefakt.

## Provenienz

- Upstream: `github.com/restic/restic`, Version `v0.19.1`.
- Release-Commit: `6aa3a516ce654808a1f28f9fa21e9b7c8e6e90bf` (aufgelöster Tag).
- Go-Modul-Quellchecksumme: `h1:LQKkar5/gYxay1OCokAWPBNiLr1lTpt4fhdM4GWqSlY=`.
- Go 1.27.3, offizielles Multi-Arch-Image per Digest im Dockerfile gepinnt.
- `go.mod` und `go.sum` enthalten den mit Go aufgelösten Abhängigkeitsstand. Es werden keine
  Go-Quellcodeänderungen auf Restic angewandt; Bibliotheken werden aus geprüften Go-Modulen bezogen.
- Upstream-Lizenz wird im App-Image unter `/usr/share/licenses/restic/LICENSE` mitgeliefert.

Die gegenüber dem offiziellen Binärstand relevanten Sicherheitsaktualisierungen:

| Baustein | Offizielles Image | Sicherheitsbuild |
|---|---|---|
| Go | 1.26.4 | 1.27.3 |
| github.com/klauspost/compress | 1.18.6 | 1.18.7 |
| golang.org/x/crypto | 0.52.0 | 0.58.0 |
| golang.org/x/net | 0.55.0 | 0.62.0 |
| golang.org/x/text | 0.37.0 | 0.43.0 |
| google.golang.org/grpc | 1.81.1 | 1.83.2 |
| github.com/go-jose/go-jose/v4 | 4.1.4 | 4.1.6 |

Die höheren Crypto-/Text-Versionen ergeben sich aus den Anforderungen von x/net 0.62.0;
es genügt nicht, nur einzelne frühere Fix-Versionen in das alte Modulfile einzutragen.
Der Go-Compiler wird mit `GOTOOLCHAIN=local` festgehalten. `go mod verify`,
`go build -mod=readonly` und die kurzen Upstream-Tests der REST-/S3-/SFTP-Backends laufen im Build.
Das resultierende Binary wird zusätzlich durch einen echten verschlüsselten Backup-/Restore-
Integrationstest und den Trivy-Image-Scan geprüft.

## Aktualisieren

1. Neue stabile Restic-Version und offizielle Quell-/Image-Artefakte prüfen. Compiler-Digest und
   Modulchecksumme aus der tatsächlichen Quelle ermitteln, keine Werte schätzen.
2. Bei eigenem Build: Upstream-Code isoliert mit Go laden, Source-Sum prüfen, gewünschte Updates
   mit Go auflösen, `go mod tidy`/`go mod verify` ausführen und beide Lockfiles übernehmen.
3. Backend-Tests, Build, echten verschlüsselten Roundtrip, Python-Prüfungen und Image-Scan ausführen.
   Keine pauschalen CVE-Ausnahmen oder Absenkung der Scan-Grenzen.
4. Wenn ein offizielles stabiles Binärimage alle relevanten Fixes enthält und dieselben Prüfungen
   besteht, kann ein eigener Folge-PR wieder auf dessen gepinnten Digest umstellen (ADR 0013).

## Quellen

- [Restic 0.19.1](https://github.com/restic/restic/releases/tag/v0.19.1)
- [Quellstand](https://github.com/restic/restic/tree/6aa3a516ce654808a1f28f9fa21e9b7c8e6e90bf)
- [Go-Prüfsummendatenbank](https://sum.golang.org/lookup/github.com/restic/restic@v0.19.1)
