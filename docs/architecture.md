# Architecture — Executive Decision (v1.0)

**Status:** Round 1 · Lead-authored. OCErp-specific reuse verdicts live in `reuse-matrix.md`
(informed by the parallel audits); this doc defines **our** architecture.

## A. Executive decision

**Build a focused, layered monorepo with pure-Python domain engines behind a
thin FastAPI service, a React/TypeScript frontend, PostgreSQL for state, an
S3-compatible object store for files, and a provider-agnostic AI boundary —
with zero code copied from OCErp (AGPL).** We adopt OCErp's *proven patterns*
(module manifests, validation-rule registries, takeoff→BOQ linking, evidence
overlays) and its permissively-licensed *dependency choices* (ezdxf, pdfplumber,
openpyxl, rapidfuzz — all MIT/BSD), but every line of our code is written fresh
against the contracts in `domain-model.md` and `api-contract.md`.

Five decisions define the system:

1. **Pure engines, thin shell.** `core/`, `ingestion/`, `takeoff/`, `boq/`,
   `pricing/`, `exports/` are framework-free Python packages with explicit
   input/output types. FastAPI `backend/` only orchestrates persistence, jobs and
   auth. Pure engines → deterministic, fast, dependency-light tests.
2. **Determinism boundary.** Geometry→quantity computation never calls AI, the
   network, or the clock. AI writes only to suggestion/insight tables
   (enforced by package layering: `classification/` imports nothing from
   `takeoff/`; `takeoff/` cannot import `classification/`).
3. **Provenance everywhere.** Every measurement carries rule id, engine version,
   input refs, and ≥1 evidence link — enforced as a data invariant, not a
   convention. Exports embed the provenance sidecar.
4. **Human gates at the three trust points:** scale confirmation, exception
   review, BOQ approval. Auto-anything only *proposes*.
5. **Boring, permissive stack.** Postgres + filesystem/S3 + one worker process +
   Docker Compose. No Redis/Kafka/vector-DB/microservices until a measured need
   exists (see `non-goals.md`).

## B. System diagram

```
┌─────────────────────────────  frontend/ (React+TS+Vite)  ─────────────────────┐
│  Upload · Progress · Drawing Viewer (tiles + SVG geometry overlay + highlights)│
│  Review workspace (exceptions, evidence panel, corrections) · BOQ grid · Export │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                │ REST /api/v1 (OpenAPI-first) + SSE events
┌───────────────────────────────▼───────────────────────────────────────────────┐
│ backend/  FastAPI app — auth, routers, persistence (SQLAlchemy 2), job runner  │
├────────────────────────────────────────────────────────────────────────────────┤
│ Domain services (pure Python, no framework imports):                          │
│  core/         domain types, units, scale, states, provenance, rule registry   │
│  ingestion/    pdf · dxf · raster → normalized Geometry + source handles        │
│  takeoff/      deterministic rules: walls, rooms, areas, lengths, counts        │
│  classification/ AI boundary: provider-agnostic, structured outputs, confidence │
│  provenance/   evidence assembly, highlight rects/paths, audit writer           │
│  review/       exception lifecycle, review actions, correction semantics        │
│  boq/          assembly from measurements, sections, recompute+diff              │
│  catalog/      items, search (rapidfuzz + embeddings later), mapping validation  │
│  pricing/      rates, markup, totals (integer minor units, banker's rounding)     │
│  exports/      csv · xlsx (openpyxl) · pdf (reportlab) + provenance sidecar      │
└───────┬──────────────────────┬───────────────────────┬─────────────────────────┘
        │                      │                       │
   PostgreSQL 16          S3-compatible store      AI providers (LLM APIs)
   (state + jobs queue)   (raw files, tiles,       via classification/ only:
                         export artifacts)         vision + structured outputs
```

## C. The measurement pipeline (drawing → BOQ, concretely)

```
1. UPLOAD        signed upload → virus/format scan → store → DrawingFile(sha256)
2. PARSE (job)   per format:
                 DXF   : ezdxf → entities by layer/block → normalized Geometry
                         (drawing units from $INSUNITS header — never guessed)
                 PDF   : pdfplumber → vector paths + text tokens; pypdfium2 → page tiles
                 raster: store + AI-vision text/region pass; NO auto-measurement
                         (human draws with on-screen tools, scale confirmed first)
3. SHEETS        page/layout → DrawingSheet; AI proposes sheet type (plan/…)
4. SCALE         auto-DETECT proposes (DXF header / scale bar / dimension text)
                 ── HUMAN GATE: POST /sheets/{id}/scale/confirm ──
5. ELEMENTS      takeoff rules assemble geometry → Elements (+AI classification,
                 confidence, stored as suggestions until confirmed)
6. MEASURE       deterministic rules → Measurements (state machine; exceptions)
7. REVIEW        ── HUMAN GATE: evidence highlighting, corrections (audited) ──
8. MAP           measurements → CatalogueItem mappings (AI suggests; human confirms)
9. BOQ           BoqItem per mapping + manual lines; rates; markups; recompute+diff
10. APPROVE      ── HUMAN GATE: validation report; zero blockers required ──
11. EXPORT       csv/xlsx/pdf + JSON provenance sidecar + manifest (immutable)
```

Capability honesty (V1): full automation on **DXF**; **PDF** = vector-extracted
polylines/areas proposed for confirmation + dimension-text understanding;
**raster** = assisted manual takeoff. Never presented as equal — the UI states
per-sheet what was measured how.

## D. Technology choices (and rejected alternatives)

| Concern | Choice | License | Why / rejected alternative |
|---|---|---|---|
| API framework | FastAPI + Pydantic v2 | MIT | Async, OpenAPI-native. Rejected Django (batteries we don't need), Flask (less schema-first). |
| DB | PostgreSQL 16 + SQLAlchemy 2 + Alembic | MIT/BSD/PostgreSQL | JSONB for geometry, strict types, mature migrations. Rejected Mongo (provenance wants relations), SQLite-prod (concurrency). SQLite allowed only in pure-engine unit tests that never touch DB. |
| Job queue | Postgres rows + `FOR UPDATE SKIP LOCKED`, one worker role | — | Zero extra infra at solo scale. Celery+Redis deferred until measured need. |
| File storage | S3-compatible iface (boto3) + local-FS dev adapter + MinIO | Apache/MIT | Files behind signed URLs; never web-root. |
| DXF parsing | **ezdxf** | MIT | The reference standard; OCErp uses it too. |
| PDF text/vector | **pdfplumber** (pdfminer.six) | MIT | Lines, curves, text tokens. Rejected PyMuPDF — **AGPL** (see license-analysis.md). |
| PDF render/tiles | **pypdfium2** | Apache-2.0/BSD | Fast page rasterization for the viewer. |
| Raster images | Pillow + AI vision | MIT-CMU | No local OCR model in V1; PaddleOCR (OCErp's choice) is heavy/GPU-leaning — deferred. |
| Geometry math | **Shapely 2** | BSD | Robust polygon ops (union, contains, area). |
| Fuzzy matching | **rapidfuzz** | MIT | Deterministic pre-filter for catalogue search. |
| Embeddings/vector search | deferred (rapidfuzz + LLM re-rank in V1) | — | OCErp carries lancedb/fastembed; we add only when search quality demands it. |
| XLSX / PDF export | openpyxl / reportlab | MIT / BSD | Permissive, no AGPL cascade. |
| AI providers | thin provider abstraction; structured (JSON-schema) outputs; vision-capable models | API terms | Provider swap is config, not code. All prompts/responses logged with hashes. |
| Frontend | React 18 + Vite + TS + TanStack Query + Zustand + Tailwind | MIT | Standard hiring pool; TanStack handles SSE/server state cleanly. |
| Drawing viewer | server tiles (PDF/raster) + client SVG overlay (DXF/normalized geometry) | — | One highlight/overlay system for all formats. |
| Testing | pytest + hypothesis (engine math) · vitest · Playwright (E2E) | MIT/Apache | Property-based tests for geometry/rounding edges. |
| Observability | structlog JSON, /healthz /readyz, request ids, Prometheus /metrics | MIT | Log→Loki later; structured from day 1. |
| Deploy | Docker Compose (app+worker+postgres+minio+caddy) on one VPS; GH Actions CI | — | Boring, cheap, observable. K8s explicitly rejected for V1. |

## E. Package layering rules (enforced in CI by import-linter)

```
backend  → may import: core, ingestion, takeoff, classification, provenance,
           review, boq, catalog, pricing, exports
takeoff  → may import: core ONLY            (determinism boundary)
ingestion→ may import: core ONLY
classification → may import: core ONLY      (AI boundary; also cannot import takeoff)
boq      → may import: core
pricing  → may import: core, boq
exports  → may import: core, boq, pricing
core     → imports nothing (except stdlib + typing)
```

`import-linter` in CI fails any PR violating the graph. This makes
"AI can never invent quantities" a *build-time property*, not a code-review hope.

## F. Key flows through the states (see domain-model.md for full machines)

- **Run:** QUEUED→RUNNING→COMPLETED_WITH_EXCEPTIONS is the *normal* outcome;
  exceptions populate the review queue. FAILED only for infra/engine faults.
- **Scale:** PROPOSED (any auto-method) → CONFIRMED (human POST only). Engine
  refuses to measure unconfirmed sheets (exception `SCALE_UNCONFIRMED`, BLOCKING).
- **Approval:** validation report computed server-side: blockers = unresolved
  exceptions, unmapped measurements, unpriced items (unless PC sums), stale
  upstream changes. Export endpoint re-verifies (409 otherwise).

## G. Performance targets (V1)

- DXF 50k entities: parse+normalize < 60s; viewer first paint < 3s (tiled/leveled).
- PDF 100 pages: text+vector extract < 5 min on worker; per-page progress SSE.
- BOQ recompute of 5k items: < 2s; export XLSX 5k rows < 10s.
- API p95 < 300ms excluding jobs. All list endpoints cursor-paginated.

## H. Security posture (V1)

- JWT (short-lived) + refresh; per-project scoping enforced in every query.
- Uploads: size caps, extension+magic-byte validation, virus scan, private storage.
- Rate limiting on auth + upload + AI endpoints; AI provider keys server-side only.
- Audit log append-only (no UPDATE/DELETE grants on the table role).
- Dependency scanning (pip-audit, npm audit) in CI; gitleaks in pre-commit.

## I. Why not X (decision log)

- **Why not fork OCErp and strip modules:** 190 modules, AGPL-3.0, ERP data model
  (projects/contracts/procurement) baked into the schema; stripping costs more than
  building clean and importing patterns + (license-checked) catalogue data.
- **Why not microservices:** solo-team velocity; a modular monolith with enforced
  layering gives the same boundaries without the operational tax.
- **Why not copy OCErp's pack data wholesale:** regional packs embed legal/tax
  rules and their own attribution; V1 imports only a checked catalogue subset
  (decision recorded per-pack in license-analysis.md).
- **Why Postgres queue over Celery:** one async pattern, one failure domain;
  retries/idempotency are ours to own either way.
