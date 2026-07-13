# ai-prototype-accelerator — Container Image
# Static scaffold file — do NOT modify when processing spec.yaml.
#
# BASE_IMAGE: defaults to python:3.11-slim (cold build installs everything).
# For fast builds, publish the dependency base once with
# accelerator/scripts/publish-base-image.sh and set deployment.base_image in
# spec.yaml — the RUN layers below detect what the base already provides and
# become near-instant no-ops.

ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE:-python:3.11-slim}

WORKDIR /app

# System dependencies — skipped when the base image already ships curl
RUN command -v curl >/dev/null 2>&1 || \
    (apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*)

# Python dependencies — a no-op resolve (~seconds) on the prebuilt base
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/
COPY agents/ ./agents/
COPY frontend/ ./frontend/
# db/ is needed at runtime for /admin/reset to re-run the seed script.
COPY db/ ./db/

# Non-root user for security — tolerate a base image that already created it
RUN (id appuser >/dev/null 2>&1 || useradd -m -u 1000 appuser) && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
  CMD curl -f http://localhost:80/health || exit 1

CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "80", \
     "--workers", "1"]
