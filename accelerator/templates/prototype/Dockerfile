# ai-prototype-accelerator — Container Image
# Static scaffold file — do NOT modify when processing spec.yaml.

FROM python:3.11-slim

WORKDIR /app

# System dependencies — Cosmos DB SDK is pure Python (no native libs needed)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/
COPY agents/ ./agents/
COPY frontend/ ./frontend/
# db/ is needed at runtime for /admin/reset to re-run the seed script.
COPY db/ ./db/

# Non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
  CMD curl -f http://localhost:80/health || exit 1

CMD ["uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", \
     "--port", "80", \
     "--workers", "1"]
