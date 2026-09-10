# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-10 (Round 4 product slice)
**Roadmap:** ../implementation-roadmap.md (retained)

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE (historical) | Decision documents + git history; dedicated round-1-report.md absent |
| 2 | Foundation | COMPLETE (historical) | Round 2 report and recorded remote CI 34273889351; deferred items retained |
| 3 | Vertical slice + trust-hardening gate | COMPLETE (historical) | All nine defect areas closed with regressions; pushed, CI green |
| 4 | Product slice (persistence, API, UI, E2E) | COMPLETE locally — push/CI next | Upload→parse→scale gate→run→BOQ gates→export through the real UI; browser E2E green; all gates verified again in a clean worktree with fresh install |
| 5 | Full takeoff engine | NOT COMPLETE | Rooms/floors/openings/deductions, PDF/raster candidates |
| 6 | AI + review workspace | NOT COMPLETE | Advisory provider gateway, classification, review, audit/corrections |
| 7 | BOQ + pricing + approval | NOT COMPLETE | Catalog/mappings, persistent rates, recompute/diff, approval services; core row/pricing/approval-context + BOQ gates + export exist |
| 8 | Exports + hardening | NOT COMPLETE | XLSX/PDF/sidecar, perf/security/E2E-in-CI/deploy; deterministic approval-gated CSV + artifacts exist |

## Current checkpoint (Round 4 — the product slice)

- **Python: 344 passed, 0 skipped** (live PostgreSQL) — Round 3 trust suites
  all still green (determinism pins, trust-hardening, wall detection,
  assembly, DXF parser).
- **Browser E2E: 1 passed (8.6s)** — register → project → upload DXF → parse
  → pre-gate refusal (Start run disabled) → human scale confirmation → run
  → measurements with state badges → BOQ build → submit → complete review →
  approve → export CSV (sha + download link).
- **mypy strict:** 84 files clean; **ruff:** clean; **import-linter:** 9
  kept, 0 broken; **reference guard:** clean (151 files); banned-package
  check clean.
- **Frontend:** 46 vitest passed; tsc strict + Vite build clean.
- **Clean-worktree CI reproduction:** fresh venv + `uv pip install
  -e .[dev,ingest,geo]`, all gates green (this is how the undeclared
  `rapidfuzz` dependency was caught and fixed before it could break CI).
- **Six latent defects closed with regressions during integration** (queue
  param bug, `-m` worker registry split, honest browser mimes rejected,
  asyncpg UUID/str crashes, store-mirror render loop, reviewed-state dead
  end) — full detail in [round-4-report.md](round-4-report.md).
- **Commits:** 8 ticket-sized commits for Round 4 (foundation, lead slice,
  upstream slice, frontend slice, integration, E2E, gitignore) on top of the
  docs reconciliation commit.

## Round 3 trust maturity (unchanged, regression-enforced)

All nine defect areas remain closed with regression tests: wall pairing
(finite congruent support, reciprocal unique, order-independent), evidence
enforcement at the measurement boundary, content-bound replay identity,
per-handle refusal of unsupported DXF, parser-warning blocking, scale/sheet
validation with the mandatory human gate, evidenced-MEASURED-only BOQ
assembly with approval-bound deterministic export, and 9 architecture
contracts with violation-proving guard tests. Round 4 built the product on
top of these invariants without weakening any of them.

Next: push + remote CI for the Round 4 series, then Round 5 candidates
(review workspace depth, BOQ editing endpoints, xlsx/pdf export,
list-runs/list-exports, catalog UI, sheet tiles, deploy). No speculative
percentage or ETA.
