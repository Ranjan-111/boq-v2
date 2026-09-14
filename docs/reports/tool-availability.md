# Tool Availability — Round 9 Checkpoint

**Verified:** 2026-09-12 · **Environment:** local macOS workspace with the
PostgreSQL and MinIO containers available.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; repository edits and full-stack local orchestration completed |
| PostgreSQL | `boqv2-postgres-1` healthy on 5432; live DB at Alembic head `a1f4c0d2e9b3` (no R8 migration) |
| Python suite | **790 passed, 0 failed** against live PostgreSQL + live MinIO (+5 perf benchmarks, 12.64s) |
| Frontend | **79 Vitest passed**; TypeScript/Vite build passed |
| Browser E2E | **2 passed** (DXF + PDF journeys) with API :8099, worker, Vite :5173, Chromium, and PostgreSQL |
| Ruff / mypy | ruff clean; strict mypy clean across 108 source files |
| Architecture | import-linter **9 kept, 0 broken** |
| Reference guard | clean; 268 tracked files checked |
| Hypothesis | 6.167.1 (MPL-2.0, approved dependency form) — 37 property tests, deterministic profile (3 identical runs) |
| CI workflow | R9 remote run **34668532001** green on `e811368` — all nine jobs incl. the perf benchmarks and the GHCR publish |
| MinIO | `boqv2-minio-1` (quay.io) exercised live: 12 S3 adapter tests + prod-compose smoke + least-privilege boq-app policy proof |
| Docker | image build succeeded locally; the CI `docker` job mirrors it |
| Export libraries | openpyxl and reportlab installed and exercised; XLSX/PDF bytes validated and deterministic |

Round 9 exercised the hypothesis property harness (6.167.1), the golden
serializer, and cProfile (in-test hotspot recording for the 50k parse);
plus everything from R8: openpyxl/reportlab, boto3 1.43.92 with
the `s3` extra, Docker/Compose against the quay.io MinIO image, and the
Playwright second journey. The final live run had no skips and 2
warnings — both the known InsecureKeyLengthWarning class raised by the
deliberate short test secrets (the pre-existing test_tokens pin and the new
login roundtrip under the 31-byte `ci-secret`).

Installed/runtime tools exercised include Python 3.13, pytest 9.1.1,
SQLAlchemy asyncpg, Alembic, ruff, mypy 2.3.1, import-linter 2.15, Node/Vite,
Vitest, Playwright 1.63 Chromium, Docker Compose PostgreSQL 16 + quay.io
MinIO, boto3 1.43.92, openpyxl, and reportlab.

## Round 10 follow-up verification — 2026-09-14 (live re-verified)

| Capability | Actual result this checkpoint |
|---|---|
| Python regression suite | 692 passed, 6 skipped (perf gated by `BOQ_PERF=1` as designed) |
| Integration suite | 149 passed, 1 skipped against live PostgreSQL + live MinIO |
| Golden replays | 31 cases green under engine 0.9.0 (4 new junction fixtures); pdf__curves records the R10 candidate semantics |
| Frontend | 135 Vitest tests passed; tsc + Vite build passed |
| Ruff / strict mypy | clean / clean (111 source files) |
| Architecture | import-linter 9 kept, 0 broken |
| Browser E2E | all six journeys passed on the full R10 stack (api :8099 + worker + vite :5173, live Postgres) |
| Remote CI | nine jobs green on the base commit `0e8235a` (run 34845002372), including e2e and publish |
| AI provider | no model credentials/configuration present; HTTP provider path remains configuration-gated |
