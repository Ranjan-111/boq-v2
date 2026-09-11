# Round 7 Report — BOQ Exports, Review Surfaces, and Trust Hardening

**Verified:** 2026-09-11 · **Verdict:** Round 7 implementation scope is
complete locally. The existing Round 6 architecture was extended in place;
no API/browser integration redesign or ERP surface was introduced.

## What Round 7 completed

- Added deterministic XLSX and PDF writers beside the existing CSV writer.
  All three formats use the same server-side approval, blocker, evidence,
  measurement-identity, stale-digest, currency, and recomputation gates.
- Added a deterministic JSON provenance sidecar for every successful export.
  The sidecar records BOQ rows, durable measurement identities, source drawing
  identity, rule and engine inputs, source handles, and evidence links. Its
  SHA-256 and storage key are recorded in the export manifest and exposed as a
  separate provenance download link.
- Added project-scoped run history and BOQ export history endpoints, including
  status filtering, deterministic newest-first ordering, ownership checks, and
  honest malformed-id 404 behavior.
- Added the catalogue management UI and audited measurement-to-catalogue
  mapping workflow. Mapping remains DRAFT-only, unit-compatible, evidence-
  gated, and refuses ambiguous or already-mapped candidates.
- Added the audited AI suggestion-apply flow. Only the explicit
  `element_classification` kind can be applied; it changes classification
  provenance only and cannot write quantities, measurement states, or BOQ
  rows. A required human reason is persisted in the audit trail.
- Added raster parsing to the real background parse job. Raster sheets persist
  with unknown scale and zero deterministic geometry. The authenticated pixel
  preview is clearly labelled as review evidence; it cannot create measured
  quantities or bypass the human scale rule.
- Added the browser E2E composition to CI (PostgreSQL, migrations, API,
  worker, Vite, and Playwright Chromium) and documented the matching Makefile
  command. Also corrected the Makefile Alembic argument order.
- Fixed the frontend AI action regression found during browser verification:
  an inactive React Query job hook reported `isPending`, permanently disabling
  “Run AI analysis”. The action now distinguishes “no job” from an active job,
  with a focused regression test.

## Files and modules changed

The main implementation areas are `.github/workflows/ci.yml`, `Makefile`,
`backend/app/api/{boqs,drawings,review,runs,scope}.py`,
`backend/app/services/{boq_service,parse_service,review_service}.py`,
`exports/{csv_export,pdf_export,xlsx_export,provenance_sidecar}.py`, and the
Round 7 frontend surfaces in `frontend/src/components/{BoqTab,CatalogTab,
DrawingsTab,ExportsTab,RunsTab}.tsx`, `frontend/src/lib/{apiClient,
jobPolling,reviewHelpers}.ts`, and the Playwright journey. Targeted backend
and frontend regression tests were added alongside those changes.

## Verification actually run

| Check | Result |
|---|---|
| Full Python suite against live PostgreSQL | **647 passed, 1 warning** (JWT insecure test key warning); no skips |
| Backend export integration | **5 passed**; CSV/XLSX/PDF gates and sidecar chain verified |
| Backend drawing/parse integration | **19 passed**; raster parse + authenticated preview verified |
| Backend list endpoint integration | **10 passed**; run/export history and ownership verified |
| Frontend Vitest | **79 passed** |
| Frontend TypeScript/Vite build | **passed** |
| Browser E2E with local API + worker + Vite + PostgreSQL | **1 passed** (full gated journey, 10.6s) |
| Ruff | **clean** |
| Strict mypy | **clean, 104 source files** |
| Import contracts | **9 kept, 0 broken** |
| Reference-leak guard | **clean, 223 tracked files** |
| Database revision | `a1f4c0d2e9b3 (head)` verified on the live development database |

The remote CI workflow was not executed from this workspace. The CI E2E job
and service composition are present and the same journey passed locally.

## Architectural decisions

Export formats remain pure serializers behind the existing Protocol boundary;
the backend constructs trusted approval and provenance context from persisted
rows. The sidecar is a separate immutable object so a human-readable export
does not need to hide or truncate evidence. Raster data is served as pixels
for review only; it is never normalized into deterministic geometry and no
scale is inferred.

## Open work and deliberate deferrals

- Remote CI execution and deployment infrastructure still need an external
  run; this round does not claim a remote green build.
- Raster preview is available, but raster takeoff remains intentionally
  blocked until a human supplies an appropriate scale and a supported
  review/takeoff rule exists.
- PDF/vector tile rendering, DWG/IFC/RVT ingestion, S3 production storage,
  performance profiling at the documented 50k-entity/5k-row targets, and
  production security review remain later milestones.
- The product still does not include unrelated ERP modules.

## Next milestone

Run the CI workflow remotely and then begin the next input/takeoff expansion
from the frozen contracts: vector-PDF scale/review depth and additional
deterministic takeoff types. Browser/API integration is now available for
those bounded additions; it was not used as a substitute for this trust gate.
