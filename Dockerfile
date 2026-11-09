# Restic 0.19.1 aus geprüftem Upstream-Modul mit aktualisierten Sicherheitsabhängigkeiten.
# Compiler-Digest am 2026-11-09 aufgelöst; Details/Provenienz: docker/restic/README.md.
FROM --platform=$BUILDPLATFORM golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS restic
ARG TARGETOS
ARG TARGETARCH
ARG TARGETVARIANT
ENV GOTOOLCHAIN=local GOMAXPROCS=4
WORKDIR /src
RUN --mount=type=secret,id=proxy_ca --mount=type=cache,target=/go/pkg/mod \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    go mod download -json github.com/restic/restic@v0.19.1 > /tmp/source.json \
    && grep -F 'h1:LQKkar5/gYxay1OCokAWPBNiLr1lTpt4fhdM4GWqSlY=' /tmp/source.json \
    && cp -a /go/pkg/mod/github.com/restic/restic@v0.19.1/. /src/ \
    && chmod -R u+w /src
COPY docker/restic/go.mod docker/restic/go.sum ./
RUN --mount=type=secret,id=proxy_ca --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    go mod download && go mod verify \
    && go test -short -p 2 ./internal/backend/rest ./internal/backend/s3 ./internal/backend/sftp \
    && CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64} GOARM=${TARGETVARIANT#v} \
       go build -mod=readonly -p 2 -trimpath -ldflags '-s -w' -o /usr/bin/restic ./cmd/restic

# Basis-Image per Digest gepinnt (python:3.12-slim, aufgelöst am 2026-11-09).
# Aktualisierung übernimmt Dependabot (docker-Ökosystem).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=restic /src/LICENSE /usr/share/licenses/restic/LICENSE
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeitsschicht zuerst (Build-Cache); nur mit Hashes aus dem Lockfile installieren.
COPY requirements.txt .
# Der optionale Build-Secret erweitert nur für diesen Schritt die CA-Vertrauensbasis der Cloud.
# Im normalen Build bleibt das Standardverhalten erhalten; keine Session-CA in Image-Layern.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --require-hashes -r requirements.txt; \
    else pip install --require-hashes -r requirements.txt; fi

COPY app ./app

# Nicht-root mit fester UID; /data ist der einzige beschreibbare Ort (Volume).
RUN chmod -R u=rwX,go=rX /srv/app \
    && groupadd --gid 10001 kw \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin kw \
    && mkdir /data && chown 10001:10001 /data
USER 10001:10001

ENV KW_DATA_DIR=/data \
    KW_TENANTS_FILE=/config/tenants.yaml

EXPOSE 8000
# Gesundheitsprüfung steht in docker-compose.yml, nicht im Image.
CMD ["python", "-m", "app.server"]
