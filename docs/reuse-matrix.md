# Reuse Matrix — OCErp Audit (Round 1)

**Status:** v1.0 · Synthesizes parallel audits A–E (read-only inspection of the
local `reference/OpenConstructionERP-main` copy; nothing was copied out).
**License context:** OCErp code is AGPL-3.0 — verdicts below are about
*patterns, data formats and ideas*, not code copying. See `license-analysis.md`.

Verdict legend — **REUSE-DATA**: import the data (with per-item license care) ·
**REUSE-PATTERN**: re-implement the design fresh, no code copy · **ADAPT**:
re-express with reference to their structure (still our own code) ·
**REWRITE**: same capability, our own architecture · **REJECT**: not wanted.

## A. Reuse / adapt / rewrite / reject table

### Ingestion & takeoff (Audit B)

| # | OCErp component | What it actually is | Verdict | Effort | Why / benefit | Risk |
|---|---|---|---|---|---|---|
| 1 | `cad/classification_mapper.py` | Pure static mapping tables (Revit→DIN276/NRM/MasterFormat, material synonyms) | **REUSE-DATA** | 0.5 pd | Deterministic fallback classification data, not code | Low (verify data quality) |
| 2 | `boq/cad_import.py` DDC binary chain | Proprietary x86-only Windows/Linux converters (Rvt/Ifc/Dwg/DgnExporter), shelled via subprocess; Excel as canonical interchange | **REJECT** | — | Binary, platform-locked (no macOS), network-installed; Excel is a weak canonical format | High — DWG needs another path (ODA File Converter or DXF-only V1) |
| 3 | `boq/dxf_native.py` | Native ezdxf DXF→rows: shoelace area, closed-entity rules, Decimal unit factors, `measurable_entities` (modelspace-only, block-definitions dropped, refuse ambiguous sheets) | **ADAPT** | 3–4 pd | Honest unit handling + "refuse to guess" measurability rules are exactly our ethos | Low |
| 4 | `dwg_takeoff/dxf_processor.py` | ezdxf parse→canonical entity JSON (all layouts + block-defs-emitted-once, ~12 entity types, defensive layer checks) | **ADAPT** | 5–8 pd | Two-producers-one-shape normalization is the right ingestion architecture; ezdxf is MIT | Low |
| 5 | `dwg_takeoff` DWG-via-DDC-Excel path | Parses DDC Excel export of DWG into same JSON shape | **REJECT** (V1) | — | Depends on rejected binary; DWG deferred to V2 via conversion | DWG users must export DXF in V1 — documented |
| 6 | Frontend viewer libs (`dwg-takeoff/lib/measurement.ts`, `auto-quantify.ts`, `calibration.ts`, `blocks.ts`) | Canvas2D viewer + geometry kernel (shoelace, self-intersection refusal, per-layer quantify, block expansion, snap) | **REUSE-PATTERN** | 8–12 pd | Spec-grade geometry behavior incl. "never silently understate a bowtie"; we rewrite in our stack | Medium |
| 7 | Positional entity ids (`e_{index}`) | Unstable ids over layer-filtered lists; DXF handles never captured | **REJECT** (do the opposite) | 0.5 pd extra | **Our differentiator:** capture `entity.dxf.handle` + layer + type + parse hash at ingestion → stable provenance | Low |
| 8 | `takeoff/service.py` recompute authority | Server re-derives every measurement value from points×scale; client/AI values are proposals only; rejections retained for audit | **REUSE-PATTERN** | 2–3 pd | The literal enforcement of "AI never invents numbers" — our core principle, proven design | Low |
| 9 | `takeoff/scale_detect.py` | Pure regex text-layer scale detection (1:N, imperial, multilingual), confidence tiers, never auto-applies | **REUSE-PATTERN** | 2 pd | Matches "never silently guess scale"; propose→human confirms | Low |
| 10 | `takeoff/recognize.py` vector detectors | PDF vector primitives → area/length/count candidates with confidence + seeded count-by-example | **ADAPT** | 3–4 pd | Deterministic candidates on PDF vector paths (PyMuPDF → **re-derive with pdfplumber/pypdfium2** due to AGPL) | Medium (PDF extraction fidelity) |
| 11 | `takeoff/raster_recognize.py` | OpenCV room/wall candidate detection (adaptive morphology, connected components, approxPolyDP rectangularity, HoughLinesP walls), counts deliberately omitted | **ADAPT** | 3 pd | Honest raster proposals with "(verify)" reasons; cv2-headless is Apache-2.0 | Medium |
| 12 | `takeoff/plan_read.py` vision validation | Vision-LLM proposes geometry → server shoelace computes numbers → self-intersection flags + confidence caps → human confirms | **REUSE-PATTERN** | 2–3 pd | Precisely our AI doctrine, already field-proven shape | Low |
| 13 | `takeoff/pdf_extract_worker.py` | Out-of-process PDF extraction with RLIMIT_AS (OOM crash isolation) | **REUSE-PATTERN** | 2 pd | Native PDF parsers segfault; isolate in child process | Low |
| 14 | `measurement/` module | REB/ÖNORM measurement *sheets*: safe Decimal formula evaluator (AST whitelist), line = formula+variables+factor+add/deduct sign, sheet reconcile with tolerance | **REUSE-PATTERN** | 2–3 pd | "Every quantity keeps the formula that produced it" — maps onto our BOQ lines | Low |
| 15 | `markups/` | Thin CRUD for calibration storage + stamps | **REWRITE** (tiny) | 1–2 pd | We keep the 3-mode scale model (default/calibrated/per-annotation override), not the CRUD | Low |
| 16 | `eac/` engine | Rule plumbing over precomputed quantities; **clash mode raises UnsupportedOutputModeError — computes no geometry** | **REUSE-PATTERN** (rule-schema taxonomy only) | 4–5 pd if built | Selector/predicate/constraint declarative-rule taxonomy is good; the kernel itself is empty for us | Low |
| 17 | `services/cv-pipeline/` | **README-only placeholder — does not exist** | **REJECT** | — | Their own rationale (in-process detectors, not a CV service) is the transferable insight | — |
| 18 | PaddleOCR/pytesseract integration | Declared but **never invoked in takeoff**; `recovered_text` scale-source enum has no producer | **REJECT** as-is; build our own if OCR needed | 5–8 pd if built | OCErp's OCR is vapor; dimension-text OCR is *our* opportunity | Medium |

### BOQ, catalogue & pricing (Audit C)

| # | OCErp component | What it actually is | Verdict | Effort | Why / benefit | Risk |
|---|---|---|---|---|---|---|
| 19 | `boq/models.py` (5 tables) | Position hierarchy (depth 8), string-stored Decimals, rich provenance fields, link-groups, optimistic versioning; markup table with scoped overrides; activity log; snapshots; quantity-link rules | **ADAPT** | 5–8 pd | Model shapes proven; drop FX/norm/GAEB entanglement | Low |
| 20 | `boq/service.py` roll-up | `compute_boq_totals`: leaf totals → direct cost → markup stack (scoped overrides, cumulative compounding) → grand total; TOCTOU-safe lock-as-approval | **ADAPT** | (in 19) | The minimal pricing path, verified working | Low |
| 21 | GAEB import/export (`boq/importers/gaeb_xml.py`, X83/X84) + BC3 | Spec-conformant GAEB DA XML 3.3 + BC3, near-standalone | **REUSE-PATTERN** (V2) | 3–5 pd each | Not V1 (CSV/XLSX/PDF first); keep as format reference | Deferred |
| 22 | XLSX/PDF export (`router.py:3944`, `pdf_export.py`) | openpyxl inline builder; reportlab branded PDF with markup cascade + HTML-escape hardening | **REUSE-PATTERN** | 5–7 pd total | Exactly our V1 export path | Low |
| 23 | `costs/models.py` CostItem | code+region, unit, rate, currency, source, classification JSON, components JSON (resource breakdown), price freshness | **ADAPT** | 3–4 pd | Our catalogue schema, minus qdrant/parquet infra | Low |
| 24 | `cost_match/matcher.py` | Deterministic explainable matcher (accent-fold, unit normalization, 0.65 coverage + 0.35 overlap × unit factor, bands) + **4-tier queue where nothing applies until human confirms** | **ADAPT** (highest-value reuse) | 3–4 pd | Our "AI catalogue suggestion" core: deterministic score + human gate | Low |
| 25 | `costs/matcher.py` hybrid | rapidfuzz lexical first; embeddings optional; LLM only re-ranks shortlist; graceful degradation | **REUSE-PATTERN** | (in 24) | Right architecture: deterministic prefilter → AI re-rank | Low |
| 26 | `match_elements/` (13.6k lines) | Multi-source (BIM/PDF/DWG/photo) match pipeline bound to qdrant + bim_hub | **REJECT** for V1 | — | V1 needs only prefilter→match→confirm idea (captured in #24) | — |
| 27 | `approval_routes/` (5.9k lines) | Multi-step quorum/SLA/delegation approval engine | **REJECT**; take the insight | 2–3 pd | Lock-as-approval (approved_by/at on row, CAS 409), mutation-invalidates-approval, audit row shape | Low |
| 28 | labor_rates, preliminaries, allowances, waste_factors, assemblies, price_index, fx | Clean pure-Decimal math, none on the minimal pricing path | **REJECT for V1** | — | waste_factors first back-fill later (one multiplier) | — |

### Catalogue data (Audits C + E)

| # | Asset | What it is | Verdict | Why / risk |
|---|---|---|---|---|
| 29 | `data/catalog/regions/DDC_CWICR_HI_MUMBAI_Catalog.csv` | 7,187 resource-price rows (INR, Hindi labels) — **resources**, not work-item unit rates | **REUSE-DATA with attribution + license check** | The global CWICR base's own license status is marked **PENDING** in `data/catalog/README.md` — do not ship until basis recorded or replaced (license-analysis.md) |
| 30 | `packs/india-cpwd` structure JSONs (24 KB) | CPWD sub-head order, trade sections, IS-1200 unit vocabulary (cum/sqm/Rmt/…), item-code format, markup stack (7.5/7.5/7.5/3 + cess + GST, flagged UNRATIFIED) | **REUSE-DATA** | AGPL covers the JSON files as a compilation; structure facts (sub-head order, units) are non-protectable facts; safest path is re-authoring our taxonomy informed by it. No DSR rates are included (they decline to reproduce them) |
| 31 | 55,719-item CWICR work-item parquet (external DDC repo) | Remote load target of `costs/base_registry.py` | **DEFER**; verify license first | Global-base license PENDING; V1 uses hand-authored items + user bulk-import (DSR/SoR) instead |
| 32 | National bases in `data/catalog/regions/` | Italy Toscana CC BY 4.0 (attribution required), Brazil SINAPI open-data, others stated | **REUSE-DATA only with per-source attribution** | Record source + license per imported file |

### What OCErp does NOT have (must be built — this is our differentiation)

1. **Room/boundary detection from DXF wall lines** — nothing in the repo
   polygonizes rooms from wall geometry. Wall thickness, room polygons,
   door/window counting from CAD blocks: all absent. (Their 2D story is
   per-layer rollup of entity geometry + candidate proposals.)
2. **Stable drawing-entity identity** — positional ids only; DXF handles never
   captured. Our handle-level provenance must be built.
3. **OCR dimension reading linked to geometry** — declared, never implemented.
4. **A provenance sidecar in exports** — import metadata persisted, but no
   export-time full provenance chain.

## B. Minimum dependency set for OUR build

Python: `fastapi, pydantic v2, sqlalchemy 2, alembic, asyncpg/psycopg, ezdxf,
pdfplumber, pypdfium2, pillow, opencv-python-headless, shapely, rapidfuzz,
openpyxl, reportlab, structlog, httpx, python-jose/bcrypt (auth)`.
(AI providers via plain `httpx` — no vendor SDK lock-in.)
Frontend: `react, vite, typescript, tanstack-query, zustand, tailwind` +
our own canvas/SVG viewer.

Explicitly avoided: PyMuPDF (AGPL cascade), Celery/Redis (Postgres queue),
qdrant/lancedb/fastembed (rapidfuzz + LLM re-rank first), pandas/pyarrow
(not needed), DDC binaries, trimesh/pyproj (3D/geo — later).

## C. Construction-domain logic worth preserving from OCErp

1. Server-side recompute authority for every measurement value (#8).
2. Scale provenance enum on every measurement (`detected|inherited|vision|manual`)
   + never auto-apply (#9, #15).
3. The proposed→confirmed/rejected-with-retention review workflow (#8).
4. "Refuse to measure ambiguous things" rules (modelspace-only, block-defs
   excluded, single-sheet fallback only when unambiguous) (#3).
5. Formula-visible quantities (REB-style measurement lines) (#14).
6. Deterministic-first matching with human-confirmation tiers (#24, #25).
7. Markup stack with scoped overrides + cumulative compounding (#19, #20).
8. Lock-as-approval + mutation invalidation (#27).
9. OOM-isolated extraction workers (#13).
10. The four-quartet provenance pattern: `source · scale_source · confidence ·
    review_status` on anything quantity-adjacent.

## D. ERP complexity to remove

190-module loader & manifest registry → **12 plain packages** with enforced
import layering. 27 regional packs with legal/tax rule engines → **catalogue
data + one markup template per region**. Multi-currency FX, price indices,
GAEB/BC3/D81 enterprise exchange → **CSV/XLSX/PDF + sidecar**. Multi-step
approval with quorum/SLA/delegation → **one approve/reject with audit**.
Vector stores, semantic extras, DDC converters, BIM/clash/geo/pointcloud →
out entirely (see `non-goals.md`).

## E. Net build estimate for V1 (our code, informed by the audits)

| Area | Effort |
|---|---|
| core/ domain types, states, provenance, units | 6–8 pd |
| ingestion/ (DXF 5–8, PDF 5–7, raster 3) | 13–18 pd |
| takeoff/ deterministic rules (incl. room detection — new work) | 12–16 pd |
| classification/ AI boundary + prompts + guardrails | 6–8 pd |
| review/ + provenance/ (evidence, audit, corrections) | 6–8 pd |
| boq/ + catalog/ + pricing/ | 10–13 pd |
| exports/ (csv, xlsx, pdf, sidecar) | 5–7 pd |
| backend/ (API, auth, jobs, storage) | 10–12 pd |
| frontend/ (shell, viewer, review, BOQ, export) | 25–35 pd |
| tests/ + fixtures (incl. adversarial) | 12–15 pd |
| devops (CI, deploy, observability) | 5–7 pd |
| **Total (agent-accelerated, wall-clock ≈ 40–50% of this)** | **~110–135 pd** |
