# Project Progress

**Project:** boq-v2 · **Verified:** 2026-09-10 (Round 3 trust gate)
**Roadmap:** ../implementation-roadmap.md (retained)

| Round | Phase | Status | Evidence / remaining gate |
|---|---|---|---|
| 1 | Discovery + architecture | COMPLETE (historical) | Decision documents + git history; dedicated round-1-report.md absent |
| 2 | Foundation | COMPLETE (historical) | Round 2 report and recorded remote CI 34273889351; deferred items retained |
| 3 | Vertical slice + trust-hardening gate | TRUST GATE VERIFIED · product slice continues | Library DXF→walls→BOQ→CSV with adversarial refusal/evidence/replay/export-approval gates; persistence/API/viewer/browser E2E remain |
| 4 | Full takeoff engine | NOT COMPLETE | Rooms/floors/openings/deductions, PDF/raster candidates |
| 5 | AI + review workspace | NOT COMPLETE | Advisory provider gateway, classification, review, audit/corrections |
| 6 | BOQ + pricing + approval | NOT COMPLETE | Catalog/mappings, persistent rates, recompute/diff, approval services; pure row/pricing/approval-context slice exists |
| 7 | Exports + hardening | NOT COMPLETE | XLSX/PDF/sidecar, immutable artifacts, perf/security/E2E/deploy; deterministic approval-gated CSV exists |

## Current checkpoint (trust-hardening gate)

- **Python: 300 passed, 0 skipped** — including live-PostgreSQL migration and
  project-authorization tests (Docker started this session; the
  previously-always-skipped DB tests now actually run and one latent defect
  was found and fixed: batched string-UUID inserts fail asyncpg insertmanyvalues
  sentinel matching — see round-3-report).
- **mypy strict:** 66 files clean; **ruff:** clean; **import-linter:** 9 kept
  (2 new Protocol-boundary contracts + core third-party forbiddance; guard
  tests prove each contract bites by injecting real violations into isolated
  tree copies); **reference guard:** clean.
- **Frontend:** 5 Vitest tests passed; production build passed.
- **Commits:** Round 3 work still uncommitted pending final report; HEAD 79a2181.
- **Browser E2E:** does not exist (out of Round 3 trust-gate scope by instruction).

## Trust maturity (Round 3 gate scope)

Round 3's 9 confirmed defects are addressed with regression tests:

1. Disjoint walls can no longer pair (finite congruent longitudinal support
   required; infinite-line offset alone is insufficient).
2. Ambiguous pairings are refused (unique reciprocal partner required);
   ordering cannot change the outcome.
3. Evidence is enforced — empty/malformed handles on any contributing face
   produce BLOCKING missing_evidence, never MEASURED.
4. Replay identity is content-bound — geometry snapshots, source
   identity/version, scale, units, rule/engine version, selection parameters
   and tolerances all feed the digest; ordering stays deterministic; durable
   uuid5 measurement ids derive from it.
5. Unsupported DXF (circles/arcs/bulges/OCS/3D/elevation/widths, nested
   INSERT, MINSERT, clipping, partial transforms) is explicitly refused with
   per-handle warnings — never reinterpreted as lines.
6. Parser warnings propagate: measure_parsed blocks any sheet whose parse
   produced warnings, missing sheets or missing source version.
7. Scale/sheet validation: missing/zero/negative/NaN/Infinity factors,
   cross-sheet calibrations, unknown drawing units all block; human
   confirmation remains mandatory.
8. BOQ accepts only evidenced MEASURED records with durable UUID identity;
   duplicate references rejected; CSV export requires an approval context
   bound to the current rows digest, exportable status and zero unresolved
   blocking/review exceptions; any row mutation invalidates the approval.
9. Architecture: 9 enforced contracts + violation-proving guard tests; the
   engine measures through the registered rule callable; services consume
   engines via structural Protocols only.

Next: persist the run/measurements/evidence through the API, wire the
drawing viewer + review/approval workflow, then browser E2E per the Round 3
product milestone and Round 4 scope. Full detail in
[round-3-report.md](round-3-report.md). No speculative V1 percentage or ETA.
