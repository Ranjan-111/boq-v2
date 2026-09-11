# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-11 · **Checkpoint:** Round 7

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE | Decision documents and OCErp reference research |
| 2 | Foundation | COMPLETE | PostgreSQL, Alembic, jobs, frontend shell, CI, contracts |
| 3 | Vertical slice + trust hardening | COMPLETE | DXF → geometry → deterministic takeoff → evidence → BOQ → CSV and adversarial guards |
| 4 | Product slice | COMPLETE | Upload, parse, scale gate, run, BOQ, approval, export, local browser journey |
| 5 | Full takeoff engine | COMPLETE | Rooms, floors, openings, deductions, corroboration and blocker rules |
| 6 | AI + review workspace | COMPLETE | Advisory AI, raster primitives, audited corrections/overrides, audit trail, BOQ editing |
| 7 | BOQ, pricing, approval and export hardening | COMPLETE LOCALLY | XLSX/PDF, provenance sidecars, list endpoints, catalogue UI/mapping, audited suggestion apply, raster parse/preview, CI E2E composition; remote CI still unrun |

## Round 7 checkpoint

- **Python:** 647 passed, 1 warning, 0 skipped against live PostgreSQL.
- **Frontend:** 79 Vitest tests passed; TypeScript and Vite build passed.
- **Browser:** one full Playwright journey passed with API, worker, Vite, and PostgreSQL.
- **Static/architecture:** ruff clean, strict mypy clean across 104 source
  files, 9 import contracts kept, reference-leak guard clean.
- **Database:** live development database is at Alembic head
  `a1f4c0d2e9b3`.

Round 7 keeps the trust boundary explicit. Export writers remain pure and
approval-gated; their sidecar is built from persisted measurement rows,
source handles, and evidence links. Raster preview is a human review aid and
does not create normalized geometry, scale, or measured quantities. The
frontend AI action now distinguishes an absent job from an active polling job.

## Open roadmap

Remote CI execution, deployment/S3 storage, vector-PDF tiles and scale review,
performance/security review, DWG/IFC/RVT ingestion, and additional
deterministic takeoff types remain future work. Raster takeoff remains
refused until its human scale and review contract is extended.
