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
| Reference guard | clean; 243 tracked files checked |
| CI workflow | R8 remote run **34652270146** green on `ec99f33` — all seven jobs incl. MinIO-backed tests and the docker build |
| MinIO | `boqv2-minio-1` (quay.io) exercised live: 12 S3 adapter tests + full prod-compose smoke test |
| Docker | image build succeeded locally; the CI `docker` job mirrors it |
| Export libraries | openpyxl and reportlab installed and exercised; XLSX/PDF bytes validated and deterministic |

Round 8 exercised openpyxl/reportlab (unchanged) plus boto3 1.43.92 with
the `s3` extra, Docker/Compose against the quay.io MinIO image, and the
Playwright second journey. The final live run had no skips and 2
warnings — both the known InsecureKeyLengthWarning class raised by the
deliberate short test secrets (the pre-existing test_tokens pin and the new
login roundtrip under the 31-byte `ci-secret`).

Installed/runtime tools exercised include Python 3.13, pytest 9.1.1,
SQLAlchemy asyncpg, Alembic, ruff, mypy 2.3.1, import-linter 2.15, Node/Vite,
Vitest, Playwright 1.63 Chromium, Docker Compose PostgreSQL 16 + quay.io
MinIO, boto3 1.43.92, openpyxl, and reportlab.
