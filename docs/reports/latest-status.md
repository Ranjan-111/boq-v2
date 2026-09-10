# Latest Status

**Verified:** 2026-09-10 · **HEAD:** `eb3a3c1` (pushed; remote CI green)
**Current:** Round 3 — **trust-hardening gate COMPLETE and CI-green**; product
slice (persistence/API/viewer/E2E) is the Round 4 scope. A documentation
reconciliation pass (product-plan vs. reference capabilities, no code) was
completed 2026-09-10 ahead of Round 4 — see the note at the bottom.

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
.venv/bin/python tools/guards/reference_leak.py  → clean (142 tracked files)
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

## Product-plan reconciliation pass (2026-09-10, documentation only)

Five capabilities re-checked against the current reference snapshot before
Round 4 planning. No code was written or changed; evidence rows recorded in
reuse-matrix.md §A-addendum (#50–#54).

1. **DWG (A):** already planned as deferred V2 DWG→DXF conversion (T037
   formalized). OCErp's own DWG path shells out to proprietary x86-64-only
   DDC converters — validating conversion-to-DXF as the only clean route.
   No V1 change.
2. **Interactive 2D viewer (B):** already planned (T110–T119); the
   interaction checklist + the provenance round-trip (BOQ row → measurement
   → source geometry → sheet/location → evidence) were formalized as an
   explicit acceptance contract in ticket-backlog.md — the viewer is a
   review instrument, not an image preview.
3. **3D/BIM viewer (C):** previously absent from our plan — now added as
   post-V1 candidate T130 (OSS renderer, selection/inspection, BOQ row →
   linked 3D element highlight), gated on deferred IFC/BIM input. Not a
   Round 4 blocker; 2D viewer (T112/T113) remains the V1 path. OCErp's
   Three.js + COLLADA BIM viewer with BOQ↔element linking (reuse-matrix
   #51) proves the pattern buildable with OSS components — pattern only.
4. **Market rates (D):** OCErp's datasets are static GitHub-hosted files,
   not live feeds — no live market data to emulate. Regional rate snapshots
   (T094, static/imported with source+date) and the price provenance chain
   (T095) added post-V1; honesty labels STATIC/IMPORTED/DYNAMIC recorded in
   non-goals.md. A bundled dataset will never be labeled live market data.
5. **Vendor pricing (E):** the reference's vendor price lists are
   user-uploaded files; its only dynamic feed is ECB FX (out of scope for
   us). Our V1 vendor path is user-imported price lists; DYNAMIC external
   price-source APIs are a deliberate post-V1 layer (T095). **Real-time
   vendor pricing is NOT PLANNED** — no reference evidence exists, and we
   will not claim it.

Files changed by the pass: ticket-backlog.md (T037, T094, T095, T130, viewer
contract note), implementation-roadmap.md (post-V1 capability detail),
non-goals.md (deferred list + pricing-data honesty labels), README.md
(stale Round 1 status → current), reuse-matrix.md (§A-addendum), this file.
Round 4 starting point unchanged: T019 router wiring → parse jobs →
persisted runs/measurements → approval/export server-side → viewer T112/T113
→ browser E2E.
