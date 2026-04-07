# ── Stage 1: build the React frontend ────────────────────────────────────────
FROM node:22-slim AS web-builder
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build          # outputs → /build/web/dist (served from api/static)

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# matplotlib non-interactive backend needs libgomp
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY pipeline/ ./pipeline/
COPY api/     ./api/

# Copy compiled frontend into the location FastAPI serves as /
COPY --from=web-builder /build/web/dist ./api/static/

ENV PYTHONUNBUFFERED=1
ENV PIPELINES_BASE_DIR=/data/pipelines
ENV META_DB_PATH=/data/dashboard.db
ENV GZ_FILES_BASE_DIR=/data/gz

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
