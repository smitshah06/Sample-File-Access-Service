# --- 1. build the front end ------------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. runtime: Python API serving the built SPA -------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DB_PATH=/data/sample_access.db STORAGE_DIR=/data/storage FRONTEND_DIST=/app/frontend/dist \
    ENABLE_DOCS=false
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --from=frontend /build/dist ./frontend/dist
RUN useradd --system --uid 10001 appuser && mkdir /data && chown appuser /data
USER appuser
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
