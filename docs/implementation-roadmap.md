# Implementation Roadmap

**Status:** Round 1 · 10-week plan to V1 production release.
**Model:** agent-accelerated development — ideal effort ~110–135 pd maps to a
~5–6 week wall-clock at solo-developer + agent-orchestration pace, padded to
8–10 weeks for integration, hardening and real-drawing validation.

## Phase overview

| Phase | Round | Wall-clock | Exit condition |
|---|---|---|---|
| 0. Audit & decisions | A | week 0 (done) | 10 docs complete; contracts frozen |
| 1. Foundation | B | wk 1 | Contracts + DB + CI green; OpenAPI validates |
| 2. Vertical slice | C (part) | wk 2–3 | **DXF in → wall quantity → evidence → BOQ row → CSV export, E2E green** |
| 3. Full takeoff engine | C | wk 3–4 | Rooms, floors, openings, deductions; states+exceptions; PDF candidates |
| 4. Review + AI layer | D | wk 4–5.5 | AI classification/suggestions live; full review workspace; audit trail |
| 5. BOQ + pricing + approval | D | wk 5.5–6.5 | Catalogue+import, rates, markups, approval, validation report |
| 6. Exports + frontend completeness | D | wk 6.5–7.5 | XLSX/PDF/sidecar; raster manual takeoff tools |
| 7. Production hardening | E | wk 8–9 | Perf targets met; security closed; E2E green; deploy pipeline |
| 8. Beta polish & release | E | wk 9–10 | Real-drawing beta with 2–3 estimator users; docs; v1.0 tag |

## Week-by-week

**Week 1 — Foundation (Round B, mostly sequential, Lead-owned)**
Monorepo scaffold (T010), core domain types + state machines + provenance
(T011–T013), SQLAlchemy models + Alembic (T014), FastAPI skeleton + auth
(T015), OpenAPI-first + CI with import-linter + license/leak guards (T016,
T020), job queue (T017), storage + upload security (T018–T019), observability
(T021). *Contracts freeze at end of week 1.*

**Week 2–3 — Vertical slice (Round C kickoff, parallel)**
Parallel: DXF parser + handles (T030–T031), PDF parser (T032), raster
ingestion (T033), scale detection (T034), geometry kernel + rules registry +
determinism harness (T040, T041, T050), frontend shell + upload (T110–T111),
test infra + fixtures (T120 partial). Integration mid-week-3: first wall
quantity end-to-end. *The vertical slice is the round's gate — one element
type all the way through beats fifty modules half-built.*

**Week 3–4 — Full engine (Round C completes)**
Walls (T042), rooms (T043), floors (T044), openings (T045), deductions (T046),
PDF/raster candidates (T047–T048), exceptions engine (T049), adversarial
fixtures complete (T120), viewer + evidence highlighting (T112–T113).

**Week 4–5.5 — AI + review (Round D begins)**
Provider abstraction + guardrails + guard tests (T060, T065, T122),
classification + suggestions (T061–T063), exception explainer (T064),
review workspace complete (T070–T076, T114–T115), audit API (T075).

**Week 5.5–6.5 — Catalogue, BOQ, pricing, approval**
Catalogue + India starter data + bulk import (T080–T082), search (T083),
mapping validation (T084), BOQ assembly + recompute/diff (T085–T086),
rates + pricing (T090–T091), approval + validation report (T092–T093),
BOQ workspace UI (T116).

**Week 6.5–7.5 — Exports + remaining UX**
CSV/XLSX/PDF + sidecar + manifest (T100–T104), export UX (T117),
manual takeoff tools for raster (T118), shortcuts/onboarding (T119).

**Week 8–9 — Hardening (Round E, parallel)**
Perf vs targets (T125), security review (T124), E2E browser suite (T126),
accessibility (T127), deploy pipeline + prod config + backups (T128),
docs (T129), golden-run + provenance integrity suites (T121, T123).

**Week 10 — Beta + release**
2–3 real estimators run real drawings; fix friction; v1.0 tag + deployed
production instance.

## Parallelism map (what overlaps, what waits)

```
WEEK 1:   [ Foundation — sequential, Lead ]
WEEK 2-3: [DXF] [PDF] [raster] [kernel+rules] [FE shell] [test-infra]   ← 6 parallel lanes
WEEK 3-4: [walls] [rooms] [openings] [viewer] [fixtures]              ← 5 lanes
WEEK 4-6: [AI layer] [review UI] [catalog data] [pricing]              ← 4 lanes
WEEK 6-8: [BOQ UI] [exports] [E2E] [perf] [security]                    ← 5 lanes
```
Must-wait-for-contracts (T030+ after T011/T013; T049 after T041; T084 after
T080; all UI pages after OpenAPI freeze). Everything else parallelizes.

## V1 acceptance criteria (release gate)

1. E2E: upload DXF + PDF + raster → confirm scale → run → review exceptions →
   correct one quantity (audit trail) → map to catalogue → price with markups →
   approve (with a blocker correctly refusing export until resolved) → export
   XLSX + PDF + sidecar; reopen sidecar and verify full provenance chain.
2. Determinism: golden-run replay byte-identical quantities for all fixtures.
3. Guard test red-line: any AI attempt to write quantity values fails CI.
4. Perf targets met (architecture.md §G) on the 50k-entity benchmark DXF.
5. Zero critical security findings; upload hardening tested adversarially.
6. Two beta estimators complete a real takeoff unassisted.

## Post-V1 (V2 candidates, priority order)

DWG input via conversion service · OCR dimension reading linked to geometry ·
waste factors · vendor rate quotes · GAEB X83/X84 export (from public spec) ·
IFC input (read-only) · team collaboration · more regional datasets (each with
original-source license check) · waste/markup templates per region.

### Post-V1 capability detail (Round 3 reconciliation pass, 2026-09-10)

Verified against the current OCErp reference snapshot; the per-capability
evidence summary is recorded in `docs/reports/latest-status.md`
(reconciliation note, 2026-09-10):

1. **DWG input (V2, deferred — confirmed).** DWG→DXF via OSS converter
   (ODA File Converter-class) or a documented user export step; the pipeline
   then reuses the DXF parser unchanged (T037). Evidence: even OCErp's DWG
   path shells out to proprietary, x86-64-only DDC converters with
   auto-download — a licensing path we rejected in reuse-matrix #2/#5.
   Conversion-to-DXF is the only clean route; no direct DWG SDK in V1/V2.
2. **3D/BIM model viewer (post-V1/V2 candidate — newly planned, T130).**
   Render 3D elements (OSS renderer), selection/inspection, click BOQ row →
   highlight linked 3D element; extends the 2D evidence doctrine to 3D.
   Gated on IFC/BIM input existing first. Evidence: OCErp ships a Three.js
   BIM viewer with per-element COLLADA geometry and BOQ↔element linking
   (plus Cesium geospatial, which stays out of scope — reuse-matrix #51) —
   proving the pattern is buildable with permissively-licensed OSS
   components (no code copied; pattern reference only). Not a Round 4
   blocker; 2D viewer (T112/T113) remains the V1 path.
3. **Regional rate snapshots (post-V1, T094).** More regional datasets,
   hand-authored or user-imported, each with original-source license
   recorded. Honest labels: STATIC (bundled snapshot with source + date) or
   IMPORTED (user upload). Evidence: OCErp's rate datasets are static
   GitHub-hosted parquet/CSV files — no live market feed exists to emulate;
   a bundled dataset is not "live market data" and must never be labeled so.
4. **Price provenance chain (post-V1, T095).** rate → source (catalogue
   snapshot / vendor quote / manual) → quote/reference timestamp → optional
   external price-source API (DYNAMIC: on-demand fetch recorded per use).
   Ordering is deliberate: STATIC → IMPORTED → DYNAMIC; each layer lands
   only when the previous one has provenance. Real-time vendor pricing is
   NOT planned — the OCErp reference has file-upload vendor price lists and
   watched-folder connectors, and its only genuinely dynamic external feed is
   an ECB FX daily API; no reference system provides real-time vendor
   pricing, and we will not claim it either.

Nothing in this list changes V1 scope; these are future-work tickets so the
capability is planned honestly rather than surfacing ad hoc.

## Risk register (top items)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| DXF wall/room detection quality below expectations | M | H | Vertical slice first (wk 2–3) proves it early; confidence thresholds + review-first fallback (candidates, not silent results); manual takeoff tools as guarantee |
| PDF vector extraction fidelity (no PyMuPDF) | M | M | pdfplumber path coverage tested in wk 2; pypdfium2 fallback; raster fallback for scanned PDFs |
| Room polygonization edge cases (bowties, gaps) | H | M | Self-intersection refusal (MEASURED never silently wrong); exceptions route to review; adversarial fixtures |
| Catalogue data licensing | L | H | Policy locked: original-source data only + importer-first strategy (license-analysis.md) |
| Agent coordination drift (duplicate domain versions) | M | M | Contracts frozen wk 1; ownership map (agent-plan.md); Lead-only merges to core/ |
| Scope creep toward ERP features | M | H | non-goals.md is binding; every PR answers "which pipeline step does this serve?" |
| AI provider cost/caps | M | L | Cost caps (T066), provider fallback, caching |
