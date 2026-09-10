# Ticket Backlog

**Status:** Round 1 · Prioritized backlog driving Rounds B–E.
**Legend:** P0 = V1 blocker · P1 = V1 strongly wanted · P2 = V2 · epics map to
`agent-plan.md` rounds. Effort in ideal person-days (pd).

## EPIC 0 — Audit & decisions (Round A) — ✅ complete (this round)

- T001 ✅ Import OCErp into `reference/` (local-only, gitignored)
- T002 ✅ Module inventory (190 backend modules mapped, 12 groups)
- T003 ✅ Key-module deep audits (takeoff, dwg_takeoff, boq, costs, cost_match, eac, cad, measurement, markups, validation, ai, ai_estimator, approval_routes)
- T004 ✅ License analysis (AGPL §13, PyMuPDF cascade, data-compilation claim, binding policy)
- T005 ✅ Reuse/adapt/rewrite/reject matrix (32 components)
- T006 ✅ Architecture decision + stack selection
- T007 ✅ Domain model + API contract (foundational, Lead-owned)

## EPIC 1 — Foundation (Round B) — sequential, Lead-owned

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T010 ✅DONE | Monorepo scaffold: `frontend/ backend/ core/ ingestion/ takeoff/ classification/ provenance/ review/ boq/ catalog/ pricing/ exports/ tests/` + pyproject + uv/pip tooling | P0 | 1 | — |
| T011 ✅DONE | `core/`: domain types (UUIDs, quantities as Decimal, units, geometry record schema) | P0 | 3 | T010 |
| T012 ✅DONE | `core/`: state machines + transitions module (pure) | P0 | 2 | T011 |
| T013 ✅DONE | `core/`: provenance model (source handles, evidence refs, input digests) + audit-trail record types | P0 | 2 | T011 |
| T014 ✅DONE | SQLAlchemy models for all entities + Alembic baseline migration | P0 | 3 | T012, T013 |
| T015 ✅DONE | FastAPI app, auth (JWT), error envelopes, request-id middleware | P0 | 3 | T014 |
| T016 ◐PARTIAL | OpenAPI spec authored first; CI validates spec + implementation match | P0 | 2 | T015 |
| T017 ✅DONE | Postgres-backed job queue (FOR UPDATE SKIP LOCKED) + worker process + SSE progress | P0 | 3 | T015 |
| T018 ✅DONE | Storage: S3-compatible interface + local-FS dev adapter + signed URLs | P0 | 2 | T015 |
| T019 ✅DONE | Upload security (core; router wiring R3): size/magic-byte validation, virus scan hook, private storage | P0 | 2 | T018 |
| T020 ✅DONE | CI skeleton (guards; first remote run on push): lint (ruff), type-check (mypy), tests (pytest), **import-linter layering guard**, license-scan + reference-leak guard | P0 | 2 | T010 |
| T021 ◐PARTIAL | Observability baseline: structlog JSON, /healthz /readyz, /metrics | P1 | 1 | T015 |

**Gate:** contracts frozen. `domain-model.md` + `api-contract.md` become
additive-only; changes are PR'd against the docs first.

## EPIC 2 — Ingestion (Round C, parallel)

| ID | Ticket | Pri | Eff | Depends | Notes |
|---|---|---|---|---|---|
| T030 ◐R3 | DXF parser (ezdxf): entities→normalized Geometry, **capture dxf.handle for stable identity**, layers, blocks-emitted-once, INSUNITS reading | P0 | 5–8 | T011, T013 | OCErp pattern #4/#7. R3: core+INSERT+refusals done; paperspace viewports later |
| T031 ◐R3 | DXF sheet/layout detection + measurability rules (modelspace-first, refuse ambiguous) | P0 | 2 | T030 | R3: modelspace-first + paperspace refusal done |
| T032 | PDF parser: pdfplumber vector paths + text tokens (dimension candidates), pypdfium2 page tiles | P0 | 5–7 | T011 | PyMuPDF banned |
| T033 | Raster ingestion: storage + AI-vision text/region pass; NO auto-measurement | P0 | 3 | T030 (interfaces) | |
| T034 ◐R3 | Scale detection (PROPOSED only): DXF header/INSUNITS, PDF text regex, scale-bar heuristic | P0 | 2 | T032 | never auto-applies. R3: DXF proposal + confirmed-scale validation done; PDF later |
| T035 | OOM-isolated extraction worker (RLIMIT_AS child process) | P1 | 2 | T032 | |
| T036 ◐R3 | Corruption/adversarial file handling + format sniffing + fixtures | P0 | 2 | T030, T032, T033 | with H1. R3: DXF adversarial fixtures + refusals done |
| T037 | DWG input via external conversion (post-V1/V2): DWG→DXF via OSS converter (ODA File Converter-class) or documented user export step; downstream pipeline reuses the DXF parser unchanged — entity extraction, INSERT/block handling, measurement, evidence all identical. No proprietary converter dependency (DDC binary chain rejected, reuse-matrix #2/#5) | P2 | 2–3 | T030 | OCErp's own DWG path also shells out to proprietary x86-only converters; conversion-to-DXF is the only licensing-clean route |

## EPIC 3 — Deterministic takeoff engine (Round C, parallel)

| ID | Ticket | Pri | Eff | Depends | Notes |
|---|---|---|---|---|---|
| T040 ✅R3 | Geometry kernel: Shapely-based primitives, unit-safe area/length/count, self-intersection refusal | P0 | 3 | T011 | R3: done (takeoff/kernel.py) |
| T041 ✅R3 | Measurement rules registry (rule_id, versioned, replayable) | P0 | 2 | T040 | R3: done (takeoff/rules, run_rule discipline guard-tested) |
| T042 ◐R3 | Wall detection from parallel line pairs → centerlines, lengths, footprint areas | P0 | 5 | T030, T040 | **OCErp has nothing — our build**. R3: straight-wall trust policy done (finite congruent support, reciprocal unique pairing, ambiguity refused, explicit max thickness) |
| T043 | Room/space polygonization from wall lines (polygonize, fill, label-by-text-proximity) | P0 | 5 | T042 | **our build** |
| T044 | Floor area rules (Gross/Net per room aggregation, storey roll-up) | P0 | 2 | T043 | |
| T045 | Opening detection: door/window blocks by name/geometry + counts (DXF); text+vector candidates (PDF) | P0 | 4 | T030, T032 | |
| T046 | Deduction rules (openings subtracted from wall areas; MEASURED_ZERO states) | P0 | 2 | T042, T045 | |
| T047 | PDF vector candidate detectors (areas/lengths/counts + seeded count-by-example) | P1 | 3 | T032 | |
| T048 | Raster candidate detectors (OpenCV rooms/walls, honest confidences, "(verify)") | P1 | 3 | T033 | |
| T049 ◐R3 | Measurement states + exceptions engine (BLOCKING vs REVIEW; scale-unconfirmed guard) | P0 | 3 | T041, T034 | R3: measure_sheet/measure_parsed with scale gate, evidence enforcement, warning propagation, content-bound replay done |
| T050 ◐R3 | Determinism test harness: golden-run replay (same inputs → identical outputs, engine_version-stamped) | P0 | 2 | T041 | with H1. R3: digest binding + ordering determinism tested; persisted golden runs later |

## EPIC 4 — AI layer (Round C/D, parallel)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T060 | Provider abstraction: httpx-based, JSON-schema structured outputs, mandatory confidence, prompt/response audit log | P0 | 3 | T011 |
| T061 | Sheet classification (plan/section/elevation/detail) with confidence | P0 | 2 | T060, T030 |
| T062 | Element classification suggestions (+label reading) with confidence + explanation | P0 | 3 | T060, T042, T043 |
| T063 | Catalogue suggestion: rapidfuzz prefilter → LLM re-rank → 4-tier queue (nothing applies without human confirm) | P0 | 3 | T060, catalog schema |
| T064 | Exception explainer (plain-language, cites evidence) | P1 | 2 | T060, T049 |
| T065 | Numerical guardrails: AI writes only suggestion tables; type-layer + import-linter enforced; tests prove no AI path writes quantities | P0 | 2 | T060, T020 |
| T066 | Failure/retry handling, cost caps, provider fallback | P1 | 2 | T060 |

## EPIC 5 — Review workspace & provenance (Round C/D)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T070 ◐R4 | Evidence assembly: highlight rects/paths per measurement (all formats), evidence API | P0 | 3 | T013, T030–T033 | R4: per-measurement evidence API + viewer highlight (selected measurement red, prior blue); all-formats + tiles later |
| T071 ◐R4 | Exceptions UI queue: severity, filter, resolve actions | P0 | 3 | T049, F-epic | R4: run-scoped list + severity badges + inline resolve (audited) done; cross-run queue later |
| T072 | Correction workflow: audited quantity corrections (original preserved, provenance=human_correction) | P0 | 3 | T012, T014 |
| T073 | Classification overrides (element_type human > AI, recorded) | P0 | 2 | T062 |
| T074 | Scale confirmation UI (two-point calibration + confirm gate) | P0 | 2 | T034 |
| T075 | Audit trail API + viewer (who/what/when/before/after) | P0 | 2 | T013, T014 |
| T076 ◐R4 | Blocker queue: unresolved BLOCKING items list, export gate | P0 | 2 | T049 | R4: server-side unresolved-blocker check refuses approve + export (tested incl. adversarial blocker); a dedicated queue view later |

## EPIC 6 — Catalogue & BOQ (Round D)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T080 | Catalogue schema + region scoping + units reconciliation | P0 | 2 | T014 |
| T081 | India starter dataset: hand-authored ~500–1,000 CPWD-aligned items from public DSR structure + terms recorded | P0 | 4 | T080, license policy |
| T082 | Bulk import (CSV/XLSX with column mapping + preview) — users bring their own DSR/SoR | P0 | 3 | T080 |
| T083 | Search: rapidfuzz lexical + categories (+AI re-rank via T063) | P0 | 2 | T080 |
| T084 ◐R4 | Mapping: measurement → catalogue item, unit compatibility validation, unmapped = BLOCKING | P0 | 3 | T080, T049 | R4: unit-group mapping w/ project>default rate scope, unmapped reported honestly as blockers; manual mapping UI later |
| T085 ◐R4 | BOQ assembly: sections (CPWD sub-head informed), items from mappings, manual/PC-sum lines | P0 | 4 | T084 | R4: persisted multi-item draft from a run (single section), unmapped honest; manual/PC-sum lines + CPWD structure later |
| T086 | Recompute + diff on upstream change; duplicate detection | P0 | 3 | T085 |

## EPIC 7 — Pricing & approval (Round D)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T090 ◐R3 | Rates: DEFAULT/PROJECT/VENDOR scopes, integer minor units, provenance | P0 | 2 | T080 | R3: CatalogueRate minor-unit slice done |
| T091 ◐R3 | Pricing engine: qty×rate, markup stack (percentage/fixed, cumulative), section+BOQ totals, banker's rounding | P0 | 3 | T085, T090 | R3: bp-markup slice w/ recompute invariant done |
| T092 ◐R4 | Approval: submit/approve/reject with audit; lock-as-approval (CAS); stale invalidation on mutation | P0 | 3 | T085 | R4: submit/review/approve/reject via state machine, audited, REVIEWED-only approve, stale transition exists; CAS lock later |
| T093 | Validation report (blockers: unresolved exceptions, unmapped, unpriced) — server-side, export gate | P0 | 2 | T092 |
| T094 | Regional rate snapshots (post-V1): additional hand-authored/user-imported regional datasets, each with original-source license recorded (reuse-matrix #29–#32 policy). A snapshot is **static bundled/imported data with source + date** — never labeled live market data (OCErp's rate datasets are likewise static GitHub-hosted files; no live feed exists to emulate) | P2 | 3–4 | T081, T082 |
| T095 | Price provenance chain (post-V1): rate → source (catalogue snapshot / vendor quote / manual) → quote/reference timestamp → optional external price-source API (DYNAMIC: on-demand fetch, recorded per use) with per-rate provenance. Real-time vendor price feeds are NOT planned — no evidence any reference system provides one, and claiming it would violate the trust doctrine. STATIC → IMPORTED → DYNAMIC in that order; user-imported price lists (CSV/XLSX) are the V1 path | P2 | 3–4 | T090, T094 |

## EPIC 8 — Exports (Round D)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T100 ◐R3 | CSV export | P0 | 1 | T091 | R3: deterministic serializer + approval gate done |
| T101 | XLSX export (openpyxl, styled, subtotals) | P0 | 2 | T091 |
| T102 | PDF export (reportlab, branded, markup cascade) | P0 | 3 | T091 |
| T103 | Provenance sidecar (JSON: every row's chain) + export manifest + reproducibility check | P0 | 2 | T093, T100–T102 |
| T104 ◐R4 | Export artifact immutability (sha256, storage) | P0 | 1 | T103 | R4: sha256 + storage key + manifest + status persisted; byte-identical re-export proven |

## EPIC 9 — Frontend (Rounds C/D/E)

| ID | Ticket | Pri | Eff | Depends |
|---|---|---|---|---|
| T110 | App shell, routing, design system, auth screens, project CRUD | ◐PARTIAL | done R2 | T016 |
| T111 | Upload flow + job progress (SSE) | P0 | 2 | T017 |
| T112 | Drawing viewer: tiles + normalized-geometry SVG overlay, pan/zoom, layer control | P0 | 6 | T018, T030–T033 |
| T113 ◐R4 | Evidence highlighting: measurement↔drawing bidirectional | P0 | 4 | T070, T112 | R4: measurement→evidence click-highlight in the viewer (pan/zoom SVG, centerlines dashed); drawing-side + tiles later |
| T114 | Review workspace: exceptions, evidence panel, corrections, overrides, audit view | P0 | 5 | T071–T076 |
| T115 | Scale confirmation UX | P0 | 1 | T074 |
| T116 | BOQ workspace: sections/items grid, mapping picker w/ suggestions, rate editing, markups, totals | P0 | 6 | T085–T093 |
| T117 | Approval + export UX (validation report, blocker gating, downloads) | P0 | 2 | T093, T100 |
| T118 | Manual takeoff tools (raster drawings): on-screen length/area/count with provenance | P1 | 4 | T112, T074 |
| T119 | Keyboard shortcuts, empty states, onboarding tour | P1 | 2 | T110 |
| T130 | 3D/BIM model viewer (post-V1): render 3D elements (OSS renderer, e.g. three.js-class), selection + inspection, click BOQ row → highlight linked 3D element — the 2D evidence doctrine extended to 3D; requires deferred IFC/BIM input (roadmap Post-V1) to exist first | P2 | 5–8 | IFC input (deferred), T113 |

**Viewer interaction contract (T112/T113 detail — Round 3 reconciliation
pass, 2026-09-10):** the drawing viewer is a *review instrument*, not an
image preview. Acceptance for T112/T113 explicitly requires: drawing display
of parsed normalized geometry (SVG overlay over raster tiles), pan/zoom,
sheet navigation across every sheet of a drawing, layer/entity visibility
control, geometry highlighting (hover + select), measurement overlays on
their source geometry, evidence highlighting per measurement, click a BOQ
row → highlight and zoom to its source geometry, click geometry → inspect
its measurements and provenance, review annotations, and on-viewer scale
confirmation (two-point calibration, T074/T115) plus manual takeoff region
interaction (T118). Every highlight round-trips the full provenance chain:
**BOQ row → measurement → source geometry → drawing sheet/location → evidence
refs (handles + source version)**. A viewer that cannot navigate this chain
fails acceptance. The OCErp reference viewer (Canvas2D pan/zoom/select +
annotations — pattern only, see reuse-matrix #38) sets the bar, not the
ceiling.

## EPIC 10 — Hardening & QA (Round E)

| ID | Ticket | Pri | Eff |
|---|---|---|---|
| T120 ◐R3 | Adversarial fixtures: corrupt DXF/PDF, missing scale, rotated sheets, multi-storey, overlapping walls, bowtie polygons | P0 | 3. R3: DXF adversarial suite done (test_trust_hardening + parser refusals) |
| T121 | Golden-run regression suite (determinism) + property-based tests (hypothesis) on geometry/rounding | P0 | 2 |
| T122 | AI-hallucination tests: model returns numbers → engine must ignore | P0 | 1 |
| T123 ◐R3 | Provenance integrity tests: every MEASURED row has evidence; export contains full chain | P0 | 2. R3: evidence enforcement + digest binding tested; persisted chain later |
| T124 | Security review: authz matrix, upload hardening, rate limits, secrets audit | P0 | 2 |
| T125 | Performance: 50k-entity DXF < 60s parse, viewer < 3s, BOQ 5k recompute < 2s; profiling + indexes | P1 | 3 |
| T126 ◐R4 | E2E browser tests (Playwright): full workflow upload→export | P0 | 3 | | R4: the gated DXF→wall→BOQ→CSV journey green incl. pre-gate refusal; CI wiring of the service composition later |
| T127 | Accessibility pass (WCAG AA on review/BOQ screens) | P1 | 2 |
| T128 | Deploy: Docker Compose (app/worker/db/minio/caddy) + GH Actions pipeline + prod config + backup | P0 | 3 |
| T129 | Docs: user guide, API reference, runbook | P1 | 2 |

## Totals

~110–135 pd ideal effort for the V1 tickets (matches reuse-matrix.md estimate
§E; the reconciliation pass of 2026-09-10 added only post-V1 P2 tickets —
T037/T094/T095/T130, ~13–19 pd — which are outside this figure).
Critical path: T010→T014→T030/T032→T042→T043→T049→T084→T085→T091→T092→T101.
