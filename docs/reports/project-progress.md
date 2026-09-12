# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-12 · **Checkpoint:** Round 9

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE | Decision documents and OCErp reference research |
| 2 | Foundation | COMPLETE | PostgreSQL, Alembic, jobs, frontend shell, CI, contracts |
| 3 | Vertical slice + trust hardening | COMPLETE | DXF → geometry → deterministic takeoff → evidence → BOQ → CSV and adversarial guards |
| 4 | Product slice | COMPLETE | Upload, parse, scale gate, run, BOQ, approval, export, local browser journey |
| 5 | Full takeoff engine | COMPLETE | Rooms, floors, openings, deductions, corroboration and blocker rules |
| 6 | AI + review workspace | COMPLETE | Advisory AI, raster primitives, audited corrections/overrides, audit trail, BOQ editing |
| 7 | BOQ, pricing, approval and export hardening | COMPLETE | XLSX/PDF, provenance sidecars, list endpoints, catalogue UI/mapping, audited suggestion apply, raster parse/preview, CI E2E composition — pushed, remote CI green (run 34621027125) |
| 8 | Vector-PDF depth, deterministic perimeter, S3 + deploy, authz matrix | COMPLETE | PDF scale proposal + candidate emission, `room.gross.perimeter.v1`, `S3Storage` + Dockerfile/prod compose + CI docker job, cross-user authz pinned + 3 latent defects fixed, second E2E journey — pushed, remote CI green (run 34652270146) |
| 9 | Perf targets, golden-run determinism, deploy pipeline | COMPLETE | §G benchmarks measured green (recompute N+1 fixed 6.07s→0.29s), 25-fixture golden suite + 37 property tests, TLS profile + least-privilege MinIO user + GHCR publish, worker healthcheck defect fixed — pushed, remote CI green (run 34668532001, nine jobs) |

## Round 7 checkpoint (historical)

- **Python:** 647 passed, 1 warning, 0 skipped against live PostgreSQL.
- **Frontend:** 79 Vitest tests passed; TypeScript and Vite build passed.
- **Browser:** one full Playwright journey passed with API, worker, Vite, and PostgreSQL.
- **Static/architecture:** ruff clean, strict mypy clean across 104 source
  files, 9 import contracts kept, reference-leak guard clean.
- **Database:** live development database is at Alembic head
  `a1f4c0d2e9b3`.

## Round 9 checkpoint

- **Python:** 790 passed, 0 failed against live PostgreSQL + live MinIO;
  5 perf tests run separately (12.64s, all §G targets green).
- **Determinism:** 25 golden files + 53 golden tests + 37 hypothesis
  property tests — all green; hypothesis profile deterministic (3
  identical consecutive runs).
- **Frontend:** 79 Vitest tests passed; TypeScript and Vite build passed.
- **Browser:** both Playwright journeys passed with the full local stack.
- **Static/architecture:** ruff clean, strict mypy clean across 108 source
  files, 9 import contracts kept, reference-leak guard clean (268 files).
- **Deploy:** least-privilege MinIO app user proven live; caddy TLS profile
  renders; GHCR publish job wired (GITHUB_TOKEN, zero new secrets);
  no migration this round (head still `a1f4c0d2e9b3`).

Round 9's trust posture: the §G benchmarks assert the targets as written
(a miss is red with the real number, never a weakened gate); the golden
suite makes unintended engine drift impossible to miss (version-aware
verdicts, byte-identical replay for every fixture); the S3 policy grants
exactly the four object actions the Storage port performs and nothing else.

## Open roadmap

Round 9 is pushed and remotely green (run 34668532001 on `e811368`, nine jobs
incl. perf and publish; image live on GHCR). Next: real-server deployment
against the published image (host + DNS are the only missing pieces), the
viewer first-paint benchmark, and the T124/T047/T034 remainders.
DWG/IFC/RVT ingestion remain post-V1 by the frozen roadmap. Raster takeoff
remains refused until its human scale and review contract is extended.
