# syntax=docker/dockerfile:1
# boq-v2 backend image (T128 first slice).
# The FRONTEND is NOT built here — it ships separately (vite build → static
# host). See docs/deploy.md. This image is the API + worker (worker uses the
# same image with a compose command override).

# ---- Stage 1: builder — uv installs project + runtime deps into a clean venv ----
FROM python:3.13-slim AS builder
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv
WORKDIR /build
# .dockerignore keeps this copy small (no .venv/node_modules/data/.git/docs)
COPY . .
# Runtime extras only — never `dev`. s3 = boto3 adapter (S3Storage).
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python ".[ingest,cv,geo,exportlibs,s3]"

# ---- Stage 2: runtime — slim base, non-root, venv + source ----
FROM python:3.13-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --create-home --home-dir /home/boq --shell /usr/sbin/nologin boq
COPY --from=builder /opt/venv /opt/venv
# Source stays at /app: Alembic's backend/alembic.ini resolves migrations
# relative to the working directory (script_location = backend/app/migrations).
WORKDIR /app
COPY --chown=boq:boq . .
USER boq
EXPOSE 8000
# /healthz is the liveness path (backend/app/main.py — no /api prefix).
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
# Migrations are Alembic-only (no create_all): run them, then serve.
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]
