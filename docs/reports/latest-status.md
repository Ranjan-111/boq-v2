# Latest Status

**Verified:** 2026-09-11 · **Current:** Round 7 complete locally; remote CI
execution remains outstanding.

Rounds 1–6 remain completed historical milestones. Round 7 finished the
planned BOQ/export/review hardening slice without changing the product scope:

- CSV, deterministic XLSX, and deterministic PDF export share one
  approval/blocker/evidence/identity gate.
- Successful exports persist a SHA-256-bound provenance JSON sidecar with
  priced rows, measurement identities, source handles, rule/input digests,
  and evidence links; the sidecar is downloadable from the export surfaces.
- Run history, export history, catalogue management, audited catalogue
  mapping, and audited AI suggestion apply are server-backed and ownership
  scoped.
- Raster uploads now parse through the worker into an honest unknown-scale,
  zero-geometry sheet and have an authenticated pixel-only preview. Raster
  pixels never become authoritative quantities.
- The browser E2E service composition is wired into CI, and the complete
  local gated journey is green.

## Verified checks

| Check | Result |
|---|---|
| Python (`core/tests tests/unit tests/integration backend/tests`) | **647 passed, 1 warning, 0 skipped** against live PostgreSQL |
| Frontend Vitest | **79 passed** |
| Frontend build | passed |
| Browser E2E | **1 passed** with API, worker, Vite, and PostgreSQL |
| Ruff / strict mypy | clean / clean (104 source files) |
| Import contracts | 9 kept, 0 broken |
| Reference-leak guard | clean (223 tracked files) |
| Alembic | live database at `a1f4c0d2e9b3 (head)` |

The one warning is the existing JWT test using a deliberately short test
secret. No tests were skipped in the final live-PostgreSQL run. Remote CI was
not run from this workspace, so the new CI composition is locally verified
but not claimed as remotely green.

## Remaining roadmap

The next milestone is vector-PDF scale/review depth and additional
deterministic takeoff types. Production S3 storage, PDF/vector tiles,
DWG/IFC/RVT ingestion, performance/security review, and remote CI execution
remain open. Raster measurement remains deliberately refused until its human
scale and review contract is extended.
