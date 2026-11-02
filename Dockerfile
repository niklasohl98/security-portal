# Basis-Image per Digest gepinnt (python:3.12-slim, aufgelöst am 2026-11-02).
# Aktualisierung übernimmt Dependabot (docker-Ökosystem).
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Abhängigkeitsschicht zuerst (Build-Cache); nur mit Hashes aus dem Lockfile installieren.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY app ./app

# Nicht-root mit fester UID; /data ist der einzige beschreibbare Ort (Volume).
RUN groupadd --gid 10001 kw \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin kw \
    && mkdir /data && chown 10001:10001 /data
USER 10001:10001

ENV KW_DATA_DIR=/data \
    KW_TENANTS_FILE=/config/tenants.yaml

EXPOSE 8000
# Gesundheitsprüfung steht in docker-compose.yml, nicht im Image.
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
