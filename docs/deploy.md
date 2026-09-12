# Deploy (T128 — first slice)

Production composition for boq-v2: Dockerfile (backend API + worker image) +
`docker-compose.prod.yml` (Postgres 16, MinIO, API, worker, optional Caddy).
The **frontend builds separately** (vite static bundle) and is served by any
static host / CDN in front of the API — it is intentionally not part of this
composition.

## What ships today

| Piece | Status |
| --- | --- |
| Backend image (multi-stage, non-root, Alembic-on-boot) | done |
| Compose: postgres + minio + api + worker (+ caddy profiles) | done |
| S3/MinIO storage adapter (`S3Storage`, boto3) | done, live-tested against MinIO |
| MinIO dedicated least-privilege app user | **done** — `minio-init` creates `boq-app` with a policy limited to the four object actions on the app bucket; the app never touches root creds (verified live: list/create/delete-bucket all refused) |
| Registry push / image publishing | **done** — CI `publish` job pushes `ghcr.io/<owner>/<repo>:{main,sha}` on main pushes via the workflow's own `GITHUB_TOKEN` (zero new secrets; PRs build only) |
| TLS termination / public domain | **done** — caddy `tls` profile: Caddy automatic TLS (ACME) for `PUBLIC_DOMAIN` with HSTS + hardened headers; `proxy` profile stays plain HTTP for local smoke |
| Real server provisioning | **open** (needs a host + DNS) |

## Local smoke test

```bash
# 1) Secrets — compose refuses to start without them. Generate:
#    JWT_SECRET=$(openssl rand -hex 32)
#    POSTGRES_PASSWORD / MINIO_ROOT_PASSWORD: pick strong values.
cat > .env <<'EOF'
JWT_SECRET=<openssl rand -hex 32>
POSTGRES_PASSWORD=<strong password, alphanumeric only>
MINIO_ROOT_PASSWORD=<strong password, alphanumeric only>
EOF

# 2) Build + start (Postgres, MinIO, bucket init, API, worker):
make deploy-up                      # = docker compose -f docker-compose.prod.yml up -d --build

# 3) Verify:
curl http://localhost:8000/healthz   # {"status":"ok"}
curl http://localhost:8000/readyz    # {"status":"ready"} once the DB answers
docker compose -f docker-compose.prod.yml ps

# 4) Stop:
make deploy-down                     # volumes survive (pgdata, miniodata)
```

Optional reverse proxy — two profiles:

```bash
# Plain HTTP (smoke test): :80 → api:8000
docker compose -f docker-compose.prod.yml --profile proxy up -d

# Public HTTPS: Caddy automatic TLS (ACME/Let's Encrypt) for PUBLIC_DOMAIN,
# HTTP→HTTPS redirect, HSTS + hardening headers. Requires DNS pointing at
# the host and ports 80/443 reachable — ACME cannot issue for localhost.
CADDY_CONFIG=tls PUBLIC_DOMAIN=boq.example.com \
  docker compose -f docker-compose.prod.yml --profile tls up -d
```

The worker serves no HTTP port — its image healthcheck (the API's :8000
probe) is disabled for the worker; honest worker liveness is the process
itself (Docker restarts it if it dies).

## Environment variables

All values come from `.env` (or exported shell vars) via `${VAR}`
substitution — compose has **no built-in defaults for secrets** and fails
with a clear error when one is missing (`${VAR:?…}` form).

| Variable | Required | Used by | Meaning |
| --- | --- | --- | --- |
| `JWT_SECRET` | **yes** | api, worker | Auth signing key. Min 8 chars (Settings validation). Must be generated per deployment — `openssl rand -hex 32`. The dev default `CHANGE-ME-dev-only` must never reach prod; compose rejects a missing var. |
| `POSTGRES_PASSWORD` | **yes** | postgres, api, worker | DB password. **Alphanumeric only** — it is interpolated into `DATABASE_URL` without URL-escaping. |
| `MINIO_ROOT_PASSWORD` | **yes** | minio, minio-init | MinIO root password — used ONLY by `minio-init` to create the app user; the app itself never holds it. Alphanumeric only. |
| `MINIO_APP_PASSWORD` | **yes** | minio-init, api, worker | Secret key of the least-privilege `boq-app` user the app actually uses. Alphanumeric only. Rotate by changing the value and re-running (user add overwrites in place). |
| `POSTGRES_USER` | no (boq) | postgres | DB user, also interpolated into `DATABASE_URL`. |
| `POSTGRES_DB` | no (boq) | postgres | Database name. |
| `MINIO_ROOT_USER` | no (boq) | minio, minio-init | MinIO root user (init-only). |
| `MINIO_APP_USER` | no (boq-app) | minio-init | The least-privilege app user's access key. |
| `S3_BUCKET` | no (boq-v2) | minio-init, api | Bucket name; `minio-init` creates it on boot (MinIO never auto-creates buckets on S3 API calls). |
| `S3_ENDPOINT` | no (http://minio:9000) | api | Container-internal MinIO endpoint. Do not point it at localhost inside the network. |
| `STORAGE_BACKEND` | no (s3) | api, worker | `local` for local-FS mode (dev-only; needs a writable `STORAGE_LOCAL_DIR` volume). |
| `STORAGE_LOCAL_DIR` | no (./data/uploads) | api | Only used when `STORAGE_BACKEND=local`. |
| `API_PORT` | no (8000) | compose | Host port for the API. |
| `PUBLIC_DOMAIN` | **yes for `tls` profile** | caddy | Public domain Caddy issues the certificate for (ACME). The container refuses to start with `CADDY_CONFIG=tls` and no domain. |
| `CADDY_CONFIG` | no (http) | caddy | `http` (smoke-test proxy) or `tls` (automatic HTTPS). |
| `CADDY_HTTP_PORT` / `CADDY_HTTPS_PORT` | no (80/443) | compose | Host ports for caddy. |
| `ACME_EMAIL` | no | caddy | ACME account email (Let's Encrypt expiry notices). |
| `ENV`, `LOG_LEVEL` | no (prod / INFO) | api, worker | App labels. |
| `AI_PROVIDER` etc. | no (stub) | api, worker | Advisory-AI settings; empty `AI_API_KEY` keeps AI honestly disabled. |

The pydantic `Settings` class (`backend/app/config.py`) reads exactly these
upper-cased names; `DATABASE_URL` is composed internally from the
`POSTGRES_*` vars so the in-network hostname `postgres` replaces `localhost`.

## Security posture of this composition

- **No secrets in the image**: `.dockerignore` excludes `.env`; secrets are
  injected at runtime by compose.
- **Non-root container**: the image creates and runs as user `boq`.
- **Migrations on boot only** (Alembic is the only schema authority — never
  `create_all`): the api's default command runs `alembic upgrade head` then
  uvicorn; the worker waits for the api healthcheck so it never starts
  against an unmigrated DB.
- **Storage is never public**: `S3Storage.signed_url()` returns short-lived
  presigned GET URLs (default 3600 s); buckets are private.

## Backups

```bash
# Postgres — run against the compose service:
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U boq -d boq > backup-$(date +%F).sql

# MinIO objects live in the named volume `boqv2-prod_miniodata` — back it up
# as a volume, or mirror the bucket (mc lives on quay.io — MinIO is GONE from
# Docker Hub):
#   docker run --rm --network boqv2-prod_default -v boqv2-backup:/backup quay.io/minio/mc \
#     sh -c "mc alias set boq http://minio:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD && \
#            mc mirror boq/$S3_BUCKET /backup"
```

## Frontend (separate build)

```bash
cd frontend && npm ci && npm run build   # static bundle in frontend/dist
```

Host it on any static server/CDN and proxy `/api` to the API service. TLS
termination for the API is the caddy `tls` profile above; point the static
host's own TLS (or the same caddy) at the frontend if you serve it yourself.

## CI image publishing (GHCR)

Every push to `main` publishes `ghcr.io/<owner>/<repo>:main` and
`:$(short-sha)` through the workflow's own `GITHUB_TOKEN` — no registry
secrets to manage. Pull with a read-scoped token:

```bash
docker pull ghcr.io/ranjan-111/boq-v2:main   # lowercase of owner/repo
```

The package starts private to the repo; make it public (or grant access)
under the repo's Packages settings if other environments need to pull.
