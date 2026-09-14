# Round 10 Follow-up Report — Partial Takeoff, Honest AI, and Geometry Evidence

**Verified:** 2026-09-14 (live-stack + browser E2E re-verified on top of
engine 0.9.0 / commit 0e8235a)  
**Verdict:** Trust/UI remediation implemented, unit-verified, and now
verified against live PostgreSQL + MinIO and all six browser E2E journeys.

## What was fixed

- Parser warnings no longer erase surviving supported geometry. A partial parse
  emits `parse_partial` review exceptions and continues deterministic takeoff.
  A warning-only parse or a structurally invalid sheet remains blocking.
- Added a real PDF regression: a refused curved path still leaves the supported
  rectangle as a reviewable candidate with evidence.
- Added `parse_partial` guidance and an audited human resolution path in the
  exception panel. `parse_incomplete` remains source-fix-only when no safe
  geometry survived; resolving it would falsely unblock a quantity.
- The API no longer starts the deterministic stub as if it were model analysis.
  Without an HTTP model configuration, insights identify the environment as
  unconfigured and explain that no model ran. The default local UI does not
  display fabricated 5% suggestions.
- The geometry endpoint now has a read-only source-geometry fallback for a
  terminal run with no persisted classified elements. It reparses immutable
  stored bytes for viewer evidence only; it creates no measurement or BOQ
  authority.
- DXF `POINT` entities are now preserved as exact render-only geometry. A
  point is visible in the viewer with its stable handle, but is deliberately
  excluded from `measurable_count` and produces no quantity. Non-planar or
  non-finite points remain explicit refusals. Verified directly against
  `test files/external/MODEL.dxf`: one point, zero warnings, zero quantities.

## Deliberate non-changes

- Curves, circles, 3DFACE, non-planar/OCS geometry, and other unsupported
  semantics remain explicit refusals. They are never flattened into guessed
  linework. A file containing only refused geometry can honestly produce zero
  measurements.
- Deterministic quantities still require evidence and human-confirmed scale;
  `NEEDS_REVIEW` candidates are not counted as authoritative measured rows.
- No API/browser integration redesign was introduced.
- Engine measurement semantics: this slice deliberately rides on top of
  engine 0.9.0 (f496fc1) WITHOUT touching any wall or room rule —
  `parse_partial` changes only how parser warnings gate a run, never a
  measured value. The single golden that changes under it (pdf__curves)
  records the previously-leaked state honestly this time: the rectangle
  candidate surfaces as NEEDS_REVIEW with a parse_partial review exception,
  generated from the code that is actually committed.

## Files changed

Application code: `takeoff/engine.py`, `backend/app/api/runs.py`,
`backend/app/services/ai_service.py`, `frontend/src/components/RunsTab.tsx`,
`frontend/src/lib/apiClient.ts`, `frontend/src/lib/exceptionGuidance.ts`,
`frontend/src/components/GeometryViewer.tsx`, `ingestion/dxf/__init__.py`,
`core/domain/enums.py`, and `.env.example`.

Tests and contracts: `tests/unit/test_trust_hardening.py`,
`tests/unit/test_dxf_parser.py`, `backend/tests/test_run_geometry_api.py`,
`frontend/tests/geometry.test.ts`, `frontend/tests/exceptionGuidance.test.ts`,
`tests/golden/data/pdf__curves__page-0.json`, and
`frontend/e2e/gated-journey.spec.ts`.

## Verification actually run (2026-09-14, this session)

| Check | Result |
|---|---|
| Python unit suite | 692 passed, 6 skipped |
| Integration (live PostgreSQL + MinIO) | 149 passed, 1 skipped |
| Ruff / strict mypy (111 files) | Passed |
| Import contracts | 9 kept, 0 broken |
| Frontend tsc / Vitest | Passed / 135 passed |
| Browser E2E (all six journeys, full R10 stack on dev DB) | 6 passed |
| Remote CI on the base commit (0e8235a) | all nine jobs green (run 34845002372) |

## Open work

- Configure and test an approved HTTP model gateway before presenting actual
  AI suggestions. The repository does not contain model credentials.
- The full-drawing product journey (upload → parse → scale → takeoff →
  rooms → review → BOQ → pricing → approval → export) on a real
  architectural drawing was completed 2026-09-14 on `Floorplan (1).dxf`
  (248 priced rows, money-exact export, audit-complete). Friction found on
  the way is recorded in `latest-status.md` (§Product journey findings).
- Keep unsupported geometry refusal rules explicit; supporting additional DXF
  entity semantics (arcs, INSERT-ARC members) requires its own parser and
  adversarial regression slice.

## Next milestone

Real-drawing end-to-end manual pass (engine works ≠ product works), then the
next narrowly scoped parser support decision (arc flattening with
chord-tolerance provenance).
