# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-12 · **Checkpoint:** Round 8

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE | Decision documents and OCErp reference research |
| 2 | Foundation | COMPLETE | PostgreSQL, Alembic, jobs, frontend shell, CI, contracts |
| 3 | Vertical slice + trust hardening | COMPLETE | DXF → geometry → deterministic takeoff → evidence → BOQ → CSV and adversarial guards |
| 4 | Product slice | COMPLETE | Upload, parse, scale gate, run, BOQ, approval, export, local browser journey |
| 5 | Full takeoff engine | COMPLETE | Rooms, floors, openings, deductions, corroboration and blocker rules |
| 6 | AI + review workspace | COMPLETE | Advisory AI, raster primitives, audited corrections/overrides, audit trail, BOQ editing |
| 7 | BOQ, pricing, approval and export hardening | COMPLETE | XLSX/PDF, provenance sidecars, list endpoints, catalogue UI/mapping, audited suggestion apply, raster parse/preview, CI E2E composition — pushed, remote CI green (run 34621027125) |
| 8 | Vector-PDF depth, deterministic perimeter, S3 + deploy, authz matrix | COMPLETE LOCALLY | PDF scale proposal + candidate emission, `room.gross.perimeter.v1`, `S3Storage` + Dockerfile/prod compose + CI docker job, cross-user authz pinned + 3 latent defects fixed, second E2E journey — remote CI run of the R8 push outstanding |

## Round 7 checkpoint (historical)

- **Python:** 647 passed, 1 warning, 0 skipped against live PostgreSQL.
- **Frontend:** 79 Vitest tests passed; TypeScript and Vite build passed.
- **Browser:** one full Playwright journey passed with API, worker, Vite, and PostgreSQL.
- **Static/architecture:** ruff clean, strict mypy clean across 104 source
  files, 9 import contracts kept, reference-leak guard clean.
- **Database:** live development database is at Alembic head
  `a1f4c0d2e9b3`.

## Round 8 checkpoint

- **Python:** 700 passed, 0 failed, 0 skipped against live PostgreSQL AND
  live MinIO (includes the 12-test S3 adapter suite).
- **Frontend:** 79 Vitest tests passed; TypeScript and Vite build passed.
- **Browser:** both Playwright journeys passed (DXF correction + PDF
  candidates) with API, worker, Vite, and PostgreSQL.
- **Static/architecture:** ruff clean, strict mypy clean across 108 source
  files, 9 import contracts kept, reference-leak guard clean (232 files),
  pip-audit clean.
- **Deploy:** Docker image builds; the prod composition smoke-tested
  end-to-end (register → project → upload → bytes in MinIO bucket);
  no migration this round (head still `a1f4c0d2e9b3`).

Round 8 kept every trust invariant: the PDF scale proposal still only
PROPOSES (the human gate confirms), candidates arrive through the same
registered rules as NEEDS_REVIEW rows (never MEASURED), and the S3 adapter
enforces LocalStorage's declared-length contract before any network I/O.
Three latent defects were fixed with regression pins (uuid guards, login
pgproto UUID, DXF-only is_modelspace).

## Open roadmap

The R8 push's remote CI run is the standing open gate (the R7 run
34621027125 was green). After it: performance profiling against the
documented targets (T125), the golden-run determinism suite (T121), and the
deploy pipeline (registry push + TLS). DWG/IFC/RVT ingestion remain post-V1
by the frozen roadmap. Raster takeoff remains refused until its human scale
and review contract is extended.
