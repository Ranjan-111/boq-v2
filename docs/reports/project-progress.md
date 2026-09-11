# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-11 (Round 6 advisory AI + review workspace + BOQ editing)
**Roadmap:** ../implementation-roadmap.md (retained)

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE (historical) | Decision documents + git history; dedicated round-1-report.md absent |
| 2 | Foundation | COMPLETE (historical) | Round 2 report and recorded remote CI 34273889351; deferred items retained |
| 3 | Vertical slice + trust-hardening gate | COMPLETE (historical) | All nine defect areas closed with regressions; pushed, CI green |
| 4 | Product slice (persistence, API, UI, E2E) | COMPLETE (historical) | Upload→parse→scale gate→run→BOQ gates→export through the real UI; browser E2E green |
| 5 | Full takeoff engine | COMPLETE (historical) | Rooms/floors/openings/deductions on DXF + PDF; corroboration + collision doctrines; unmapped blockers; raster deferred to Round 6 |
| 6 | AI + review workspace | COMPLETE locally — push/CI next | Advisory provider + guardrails, audited corrections/overrides/audit trail, raster parse + candidates, BOQ editing + recompute/diff + validation; E2E proves the correction journey end-to-end |
| 7 | BOQ + pricing + approval | NOT COMPLETE | Catalog/mappings, persistent rates, recompute/diff, approval services; core row/pricing/approval-context + BOQ gates + export + unmapped blockers + DRAFT editing exist |
| 8 | Exports + hardening | NOT COMPLETE | XLSX/PDF/sidecar, perf/security/E2E-in-CI/deploy; deterministic approval-gated CSV + artifacts exist |

## Current checkpoint (Round 6 — advisory AI + review workspace)

- **Python: 575 passed across both suites** (backend/tests 136 live-PG +
  tests/unit + tests/integration + core/tests 439), 0 skipped — all
  Round 3–5 trust suites still green alongside the new R6 surfaces.
- **Browser E2E: 1 passed** — the journey now includes the Round 6 core:
  correct Wall 1 through the review UI → struck-through original beside
  the corrected pill → BOQ bills the corrected sum → unmapped blockers
  resolved by the human → approve → export → audit tab shows
  `correct_quantity` + `approve`.
- **mypy strict:** 98 files clean; **ruff:** clean; **import-linter:** 9
  kept, 0 broken (new AI-boundary contract included); **reference
  guard:** clean; banned-package check clean.
- **Frontend:** 63 vitest passed; tsc strict + Vite build clean.
- **New doctrines landed (regression-enforced):** durable identity is
  per-run and ambiguity is a named 409 (never first-by-order); the
  original value column is never mutated (corrected_value on the same
  row, billed on fresh builds and recompute); every project-scoped
  audit row carries project_id (the R5 writers' NULL gap closed);
  AI suggestions validate against a strict schema, quantity aliases are
  stripped at any depth, confidence clamped [0, 0.95]; raster parses to
  zero geometries with pixel-space-only "(verify)" candidates.
- **Commits:** the Round 6 series (pre-flight + cv extra + migration,
  worker A AI + raster, worker B review actions, lead BOQ editing +
  analyze, E2E + fixes, docs) — see
  [round-6-report.md](round-6-report.md).

## Round 3 trust maturity (unchanged, regression-enforced)

All nine defect areas remain closed with regression tests: wall pairing
(finite congruent support, reciprocal unique, order-independent), evidence
enforcement at the measurement boundary, content-bound replay identity,
per-handle refusal of unsupported DXF, parser-warning blocking, scale/sheet
validation with the mandatory human gate, evidenced-MEASURED-only BOQ
assembly with approval-bound deterministic export, and 9 architecture
contracts with violation-proving guard tests. Rounds 4–6 built the product
on top of these invariants without weakening any of them.

Next: push + remote CI for the Round 6 series, then Round 7 candidates
(xlsx/pdf export, list-runs/list-exports, catalog UI, suggestion-apply
flow, raster viewer, E2E-in-CI, deploy). No speculative percentage or ETA.
