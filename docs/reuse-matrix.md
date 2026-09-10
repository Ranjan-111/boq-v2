# Reuse Matrix — OCErp Audit (Round 1)

**Status:** v1.1 · Synthesizes all five parallel audits A–E (read-only inspection of the
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

### Platform architecture (Audit A)

| # | OCErp layer | What it actually is | Verdict | Why / benefit | Risk if adopted naively |
|---|---|---|---|---|---|
| 33 | Module system (manifest + topo-sort loader) | 190 modules, dir+manifest convention, kebab-mount routers; `inference` AI-transparency declarations per module | **ADAPT** (at ~15 modules scale) | The vertical-slice convention is the best idea in the repo; `inference` declarations tailor-made for AI-native products | Runtime enable/disable = route-table surgery (fragile); hidden lazy imports made `depends` decorative — we enforce boundaries with import-linter instead |
| 34 | Dependency graph | Hubs: `projects` (148 dependents), `users` (111), `boq` (24), `costs` (16), `ai` (10). Minimal drawing→takeoff→BOQ closure = **16 modules**; minimum viable = **5–9** | **REUSE-PATTERN** | Proves our 12-package target is feasible; `cost_match→dashboards` and `match_elements→bim_hub` were the only ERP-shell edges in the slice | — |
| 35 | `measurement/` pure library | No manifest/router — Decimal-exact, ORM-free, formula-carrying MeasurementLine/Sheet (REB/ÖNORM) | **ADAPT** | "Deterministic measure engine as a pure library" is exactly our `takeoff/` shape | — |
| 36 | Jobs system (`core/job_runner.py`) | JobRun row as truth, kind-registry handlers, unique idempotency key, progress updates, in-process fallback when no broker | **ADAPT** | Matches our Postgres-queue decision; handler registry + idempotency are the right contract | Skip Celery/Redis weight; handlers never import transport |
| 37 | DB/schema approach | Async SQLAlchemy + PG-only; **but** dual schema authority (create_all + boot-time "schema heal" + Alembic) and GUID-as-VARCHAR(36) mismatch documented in their own code | **ADAPT with rejection** | Alembic-only from day one, native uuid columns, no schema-heal machinery | Their docstring is the warning label |
| 38 | Frontend shell | 200+ features/, static-route App.tsx, zustand + react-query, `modules/_registry` for ~17 optional add-ons; **DxfViewer (1.9k lines) + lib/{dxf-renderer,snap,ortho,blocks,calibration,measurement,auto-quantify} is the separable asset** — the 7,086-line page around it is welded to the ERP shell | **REUSE-PATTERN** (viewer engine spec) | Viewer engine + measurement persistence behind injected API adapters is our extraction unit; zustand+react-query split confirmed | Don't port pages; build our shell fresh |
| 39 | Packs / data-as-config | Standards-as-data (`rule_packs/*.json`), country packs as installable dists (locales, onboarding, currency/tax defaults), matcher tuning as deployable JSON | **KEEP-PATTERN** | Our regional standards (IS 1200, CPWD) become data files, not code | Their silent-fallback-when-data-missing bug — we fail loudly |
| 40 | `packages/oe-sdk` | Facade re-exporting `app.core`; NOT standalone (pulls whole backend) | **REJECT** | Not needed at our scale; scaffold-CLI idea noted | — |
| 41 | `tools/costbase_pipeline` | Offline cost-data ingestion/localization/QA pipeline (produced their 77MB catalog) | **REUSE-PATTERN** | Our catalogue-import supply chain follows this decoupled-pipeline shape | — |
| 42 | Maturity signals | 1,907 backend tests, 352 migrations, 21 CI workflows incl. SBOM/signing/scorecard; regression-first test culture; but ~5K comment-lines-per-incident = complexity tax | **KEEP-PATTERN** (test culture, CI breadth) | Confirms the quality bar is achievable; their incident essays are our pre-read warnings | — |

### AI layer & validation (Audit D)

| # | OCErp capability | What it actually is | Verdict | Effort | Why / benefit | Risk |
|---|---|---|---|---|---|---|
| 43 | Multi-provider LLM client (`ai/ai_client.py`) | ~20 providers via raw httpx, BYOK, cost tracking, model-slug self-healing, JSON *extracted* not enforced | **ADAPT** | 5–8 pd | Thin no-SDK provider client is right; we need ~3 providers + schema-enforced structured outputs (their gap) | Their `extract_json` regex salvage → we enforce schemas at the API layer |
| 44 | Prompt-injection fencing | `fence_user_content()` + control-char sanitization + length caps; "drawing text is labels, never instructions" | **REUSE-PATTERN** | 1–2 pd | Directly portable doctrine for an AI-native tool that ingests untrusted drawing content | — |
| 45 | `TEXT/PHOTO_ESTIMATE_PROMPT` | Asks the LLM to *invent* quantities and rates | **REJECT** | 0 | Violates our core doctrine; OCErp's own newer modules abandoned this path | — |
| 46 | Vision plan-read (`takeoff/plan_read.py`) | Model proposes geometry+confidence → server shoelace recompute + scale plausibility belt + self-intersection test → human confirms | **ADAPT** | 8–12 pd | The highest-value AI reference in the repo — our drawing-understanding design, field-proven | — |
| 47 | Agent ReAct loop (`ai_agents/base.py`) | Clean runner: tool registry, safety caps (8 iters/20K tokens/120s), step persistence, ScriptedLLM for tests | **ADAPT, defer to V2** | 4–6 pd | Single-shot structured calls are cheaper/more predictable for V1; keep for copilot later | — |
| 48 | Trust envelope (`trust.py`) | Confidence + rationale + real-id sources + "what would increase confidence"; never fabricates | **REUSE-PATTERN** | 2 pd | Our mandatory-confidence requirement, already spec'd | — |
| 49 | Confidence calibration scoreboard | Brier score, calibration bins, ECE — measures whether confidence fields mean anything | **REUSE-PATTERN** | 2 pd | Turns "confidence" from theater into a measured signal | — |
| 50 | Cost matching hybrid | Deterministic scorer (0.65 coverage + 0.35 overlap × unit factor) → embeddings optional → LLM re-ranks grounded shortlist only (cost-capped, never-raise, clamp, append forgotten) → `flag_for_human` when nothing grounds | **ADAPT** | 3–4 pd (matcher) | "LLM reorders a grounded shortlist, never generates candidates" is our catalogue-suggestion rule | Heavy vector infra optional for V1 |
| 51 | Validation engine | Context/Registry/Rule/Severity/Report; 9.5K-line rules file + i18n messages; ERROR gates AI-apply, BOQ rules deliberately WARNING | **ADAPT (slim rewrite ~300 lines)** | 5–8 pd | Generic shape is right; our export-gate is an explicit policy choice it cleanly supports | Don't port the 9.5K rules file |
| 52 | NL→DSL rule building | Deterministic regex matchers first, LLM fallback only, AI output **round-tripped through the strict parser** (invalid = rejected, never trusted) | **ADAPT** | 4–6 pd | "AI suggests rule, engine enforces" — exactly our validation-extension pattern | — |
| 53 | Suggested-vs-confirmed separation | `source` + `confidence` (NULL = honestly not AI) + `review_status` (proposed/confirmed/rejected) + confirmed-only totals — exists in 5 places | **REUSE-PATTERN (platform invariant)** | 2 pd schema conventions | Rejected proposals retained for audit; auto-apply threshold 0.85, rest needs_review | — |
| 54 | Unified AI audit log | **Does not exist** (steps persisted in agents; job results stored; no unified prompt+response log) | **BUILD (our gap → theirs)** | in T060 | Our doctrine's evidence trail; cheap for us | — |

## A-end. Architectural lessons locked in from Audit A

1. **Their dependency graph is decorative** — runtime lazy imports cross module boundaries everywhere. Our import-linter CI guard (architecture.md §E) is not optional hygiene; it is the difference between a real architecture and a drawing of one.
2. **Alembic-only, native uuid, no schema-heal.** Their dual schema authority (create_all + heal + 352 migrations) produced documented drift.
3. **JobRun-row jobs contract** adopted for our Postgres queue design (T017).
4. **App factory < 200 lines.** Their 4,700-line `main.py` with hand-mounted aliases is the counterexample.
5. **Pack/rule-data pattern** adopted: regional standards and matcher tuning as data files, with loud failure when missing.

## D-end. AI-layer architecture decisions from Audit D

1. **Schema-enforced structured outputs** at the provider gateway (their #1 gap — everything was prompt-hoped JSON).
2. **Unified AI audit log table** for every call: fenced prompt, raw response, parsed payload, schema version, provider/model, tokens, cost, caller, trace id (their #2 gap).
3. **Single-shot vision calls per page** for V1 drawing understanding (their agent loop deferred to V2 copilot).
4. **Trust envelope mandatory** on all analytical AI surfaces; calibration scoreboard (Brier/ECE) to keep confidence honest.
5. **Deterministic-first matching**, LLM as grounded-shortlist re-ranker only; `flag_for_human` as an explicit AI action.
6. **Confirmed-only totals**: proposed rows listed but excluded; unreviewed-proposal count surfaced as WARNING so a short estimate is never silent.

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

## A-addendum. Round 3 reconciliation evidence (2026-09-10)

Rows added when five product capabilities were re-checked against the current
reference snapshot (read-only) during the Round 3→4 documentation
reconciliation pass. Same verdict semantics as section A; no code copied.

| # | OCErp component (current snapshot) | What it actually is | Verdict | Where it feeds |
|---|---|---|---|---|
| 50 | `dwg_takeoff` module suite (backend + frontend) | 2D drawing viewer: Canvas2D DXF renderer with pan/zoom/select, layers, measurement/annotation overlay, drawing-version entities API, `PATCH /drawings/{id}/scale/`, BOQ link endpoints, SVG thumbnails | **REUSE-PATTERN** | Confirms the T112 viewer shape; interaction checklist + provenance chain formalized in ticket-backlog (viewer contract note) |
| 51 | BIM 3D viewer (`BIMViewer` frontend suite + `bim_hub` backend) | Three.js-based 3D BIM viewer: per-element COLLADA/GLB geometry, click/hover selection, properties panel, measure/section/clip tools; elements ingest via DDC cad2data CSV/Excel or IFC/RVT; RVT ElementId ↔ DAE node `mesh_ref` pairing; Cesium + PointCloudViewer also present (geospatial — out of scope for us) | **REUSE-PATTERN** (post-V1, T130 only) | Newly planned capability: 3D/BIM model viewer deferred post-V1, gated on IFC/BIM input; pattern proves buildability with OSS renderer + geometry files, no code copied |
| 52 | `costs` rate datasets (base_registry + catalogue snapshots) | Cost bases are *static, GitHub-hosted parquet/CSV files* (nine families, localized markets); downloaded then queried locally — **not live market feeds** | **REJECT** (data); **REUSE-PATTERN** (import pipeline) | Grounds T094's honesty label: regional rate *snapshots* are static bundled/imported data with source + date — never "live market data" |
| 53 | `supplier_catalogs` price lists | Vendor price lists arrive as **user-uploaded files** (`POST /price-lists/{vendor_id}/import`, CSV/XLSX UploadFile); no scheduled/vendor API sync exists anywhere in the reference | **REUSE-PATTERN** (IMPORTED semantics) | Grounds T095/labels: user-imported price lists are the V1 vendor path; DYNAMIC (API price sources) is post-V1; real-time vendor pricing is NOT PLANNED |
| 54 | Dynamic external feeds (fx module) | The only genuinely dynamic external data in the reference: ECB daily FX XML fetched via httpx + World Bank PPP API | **REJECT** (multi-currency FX out of scope; non-goals) | Confirms "dynamic" ≠ "real-time"; even their dynamic feed is a daily-pull, and theirs is FX — a capability we explicitly do not build |

Addendum verdicts do not alter the V1 minimum dependency set above — #51 is
post-V1, #52–54 are data/pattern references only.

## B2. OCErp dependency graph (Audit A facts)

Hub modules by inbound dependents (of 190): `projects` **148**, `users`
**111**, `boq` 24, `costs` 16, `ai` 10, `dashboards` 4. The drawing→takeoff→BOQ
closure is **16 modules** (the 14 named + `dashboards` via `cost_match`, plus
the pure `measurement` library which is not a module at all); minimum viable
loads: 5 (price+BOQ+costs), 7 (+dwg_takeoff+markups), 9 (+cad+takeoff).

Key declared edges in our slice: `takeoff → {projects, cad}` ·
`catalog → costs` · `cost_match → {users, projects, costs, dashboards}` ·
`match_elements → {…, bim_hub}` · `ai_estimator → {…, match_elements, ai,
ai_agents}` · `validation → {projects, boq}`.

Two lessons: (1) the closure is small enough that our 12-package rewrite is
provably feasible; (2) their *declared* graph is a floor — runtime lazy
imports crossed boundaries constantly (`boq→takeoff/dwg_takeoff/costs/ai`,
`projects→takeoff/markups`), which is precisely what our import-linter guard
exists to prevent.

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
