# Tool Availability — Round 7 Checkpoint

**Verified:** 2026-09-11 · **Environment:** local macOS workspace with the
PostgreSQL container available.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; repository edits and full-stack local orchestration completed |
| PostgreSQL | `boqv2-postgres-1` healthy on 5432; live DB at Alembic head `a1f4c0d2e9b3` |
| Python suite | **647 passed, 1 warning, 0 skipped** against live PostgreSQL |
| Frontend | **79 Vitest passed**; TypeScript/Vite build passed |
| Browser E2E | **1 passed** with API :8099, worker, Vite :5173, Chromium, and PostgreSQL |
| Ruff / mypy | ruff clean; strict mypy clean across 104 source files |
| Architecture | import-linter **9 kept, 0 broken** |
| Reference guard | clean; 223 tracked files checked |
| CI workflow | E2E job and service composition are present; remote workflow was not run from this workspace |
| Export libraries | openpyxl and reportlab installed and exercised; XLSX/PDF bytes validated and deterministic |

The only test warning is the existing JWT test that intentionally uses a
short secret. The final live run had no database-related skips. Earlier
sandbox-only test attempts skipped PostgreSQL integration tests because the
database was inaccessible there; those results were superseded by the live
run above and are not counted as the Round 7 result.

Installed/runtime tools exercised include Python 3.13, pytest 9.1.1,
SQLAlchemy asyncpg, Alembic, ruff, mypy 2.3.1, import-linter 2.15, Node/Vite,
Vitest, Playwright 1.63 Chromium, Docker Compose PostgreSQL 16, openpyxl, and
reportlab. No remote service, deployment, or production S3 adapter was
claimed as verified.
