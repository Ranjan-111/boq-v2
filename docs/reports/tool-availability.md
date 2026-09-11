# Tool Availability — Round 8 Checkpoint

**Verified:** 2026-09-12 · **Environment:** local macOS workspace with the
PostgreSQL and MinIO containers available.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; repository edits and full-stack local orchestration completed |
| PostgreSQL | `boqv2-postgres-1` healthy on 5432; live DB at Alembic head `a1f4c0d2e9b3` (no R8 migration) |
| Python suite | **700 passed, 0 failed, 0 skipped** against live PostgreSQL + live MinIO (12 S3 tests included) |
| Frontend | **79 Vitest passed**; TypeScript/Vite build passed |
| Browser E2E | **2 passed** (DXF + PDF journeys) with API :8099, worker, Vite :5173, Chromium, and PostgreSQL |
| Ruff / mypy | ruff clean; strict mypy clean across 108 source files |
| Architecture | import-linter **9 kept, 0 broken** |
| Reference guard | clean; 232 tracked files checked |
| CI workflow | R7 remote run **34621027125** green on `fc6e128`; the R8 push's run (now incl. MinIO tests + docker build job) is the open gate |
| MinIO | `boqv2-minio-1` (quay.io) exercised live: 12 S3 adapter tests + full prod-compose smoke test |
| Docker | image build succeeded locally; the CI `docker` job mirrors it |
| Export libraries | openpyxl and reportlab installed and exercised; XLSX/PDF bytes validated and deterministic |

Round 8 exercised openpyxl/reportlab (unchanged) plus boto3 1.43.92 with
the `s3` extra, Docker/Compose against the quay.io MinIO image, and the
Playwright second journey. The final live run had no skips. The only
historical warning (the JWT test's deliberate short secret) no longer
appears — the R8 suite ran warning-free.

Installed/runtime tools exercised include Python 3.13, pytest 9.1.1,
SQLAlchemy asyncpg, Alembic, ruff, mypy 2.3.1, import-linter 2.15, Node/Vite,
Vitest, Playwright 1.63 Chromium, Docker Compose PostgreSQL 16 + quay.io
MinIO, boto3 1.43.92, openpyxl, and reportlab.
