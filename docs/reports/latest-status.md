# Latest Status

**Verified:** 2026-09-10 · **HEAD:** `5826387` (pushed; remote CI green)
**Current:** Round 3 — **trust-hardening gate COMPLETE and CI-green**; product
slice (persistence/API/viewer/E2E) is the Round 4 scope.

Rounds 1 and 2 remain completed historical milestones. Round 3 now contains a
working DXF → wall measurements → BOQ → approval-gated CSV library slice whose
trust boundary is adversarially regression-tested end to end.

## This checkpoint (trust gate)

All nine reproduced/inspected defects from the previous checkpoint are closed
with regression tests (see round-3-report.md "Trust gate verification"):

- Wall pairing: finite congruent support, reciprocal unique pairs, ambiguity
  refused, order independence.
- Evidence: any missing/malformed face evidence → BLOCKING; malformed refs
  (`0`, `None`, blank) refused.
- Replay: digest binds geometry content, source identity/version, scale,
  units, rule/engine versions, selection parameters; ordering deterministic;
  uuid5 measurement identity derived from it.
- Unsupported DXF geometry refused per-handle (never fake lines); parser
  warnings block measurement via `measure_parsed`.
- Scale/sheet: invalid factors, cross-sheet calibration, unknown units all
  block; human confirmation preserved.
- BOQ/export: evidenced MEASURED-only assembly, durable identity, duplicate
  rejection, approval-gated deterministic CSV with stale detection.
- Architecture: 9 import-linter contracts (including new boq/exports
  Protocol-boundary and core-third-party forbiddance), each proven to bite by
  guard tests injecting real violations into isolated tree copies.
- Foundation: creator-scoped project queries and app-owned DB lifespan
  verified against live PostgreSQL this session (Docker up; previously these
  tests always skipped). One latent test-only defect fixed (batched
  string-UUID ORM inserts vs asyncpg sentinel matching).

## Verified checks (exact)

```
.venv/bin/pytest core/tests backend/tests tests   → 300 passed, 0 skipped (live Postgres)
.venv/bin/mypy (CI package list)                  → 66 files clean
.venv/bin/ruff check .                           → clean
.venv/bin/lint-imports                           → 9 kept, 0 broken
.venv/bin/python tools/guards/reference_leak.py  → clean (112 tracked files)
git diff --check                                  → clean
frontend npm run test                             → 5 passed
frontend npm run build                            → passed
```

## Not done / not claimed (Round 4 scope)

Browser E2E (does not exist), full API/browser integration, persistence of
runs/measurements/BOQ, pip-audit/npm audit, performance suite, live job-queue
concurrency tests.

## Push + CI record

Round 3 committed in 12 ticket-sized commits (281973c..5826387) and pushed.
All five CI jobs were reproduced green in a clean `git worktree` BEFORE
pushing — that reproduction caught two would-be failures (context.md with
banned OCErp/CWICR names; a venv-path assumption in the architecture guard
test). Remote runs: 34422918941 (code, 5/5 success) and 34423132950
(docs-only follow-up, success).

## Next step (Round 4)

Wire the approved API contracts through storage/parse/run jobs, persisted
measurements/exceptions/evidence and BOQ, server-side approval/export
blockers (load trusted approval scope — never client input), upload/viewer
UI and a real browser journey. See [round-3-report.md](round-3-report.md)
NEXT ROUND HANDOFF for the ordered list.
