# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-11 (Round 5 full takeoff engine)
**Roadmap:** ../implementation-roadmap.md (retained)

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE (historical) | Decision documents + git history; dedicated round-1-report.md absent |
| 2 | Foundation | COMPLETE (historical) | Round 2 report and recorded remote CI 34273889351; deferred items retained |
| 3 | Vertical slice + trust-hardening gate | COMPLETE (historical) | All nine defect areas closed with regressions; pushed, CI green |
| 4 | Product slice (persistence, API, UI, E2E) | COMPLETE (historical) | Upload→parse→scale gate→run→BOQ gates→export through the real UI; browser E2E green |
| 5 | Full takeoff engine | COMPLETE locally — push/CI next | Rooms/floors/openings/deductions on DXF + PDF; corroboration + collision doctrines; unmapped blockers close the R4 gap; raster (T033/T048) deferred to Round 6 (AI-dependent) |
| 6 | AI + review workspace | NOT COMPLETE | Advisory provider gateway, classification, review, audit/corrections, raster AI-vision candidates (T033/T048) |
| 7 | BOQ + pricing + approval | NOT COMPLETE | Catalog/mappings, persistent rates, recompute/diff, approval services; core row/pricing/approval-context + BOQ gates + export + unmapped blockers exist |
| 8 | Exports + hardening | NOT COMPLETE | XLSX/PDF/sidecar, perf/security/E2E-in-CI/deploy; deterministic approval-gated CSV + artifacts exist |

## Current checkpoint (Round 5 — the full takeoff engine)

- **Python: 435 passed, 0 skipped** (live PostgreSQL) — Round 3/4 trust
  suites all still green (determinism pins re-verified at ENGINE_VERSION
  0.4.0, trust-hardening, wall detection, assembly, parsers, journey).
- **Browser E2E: 1 passed (8.8s)** — the Round 4 journey extended with the
  Round 5 trust closure: approve is refused with the unmapped_measurement
  blockers visible in the UI, the human resolves them through the audited
  `POST /exceptions/{id}/resolve`, then approve + export pass.
- **mypy strict:** 83 files clean; **ruff:** clean; **import-linter:** 9
  kept, 0 broken; **reference guard:** clean (180 files); banned-package
  check clean (pdfplumber only — PyMuPDF still banned).
- **Frontend:** 46 vitest passed; tsc strict + Vite build clean.
- **New doctrines landed (regression-enforced):** bare collinear gaps are
  never openings (corroboration required; `opening_ambiguous` REVIEW);
  BOQ mapping groups by (rule_id, unit) with symmetric collision settle
  (gross and net can never bill as one line); unmapped measurements BLOCK
  approve/export until a human resolves them.
- **Commits:** the Round 5 series (core contracts + fixtures, engine rooms/
  floors, openings + deductions, DXF labels, PDF slice, BOQ determinism +
  wiring, docs) — see [round-5-report.md](round-5-report.md).

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
