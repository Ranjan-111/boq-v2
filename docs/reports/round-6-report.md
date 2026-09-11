# Round 6 Report — Advisory AI, Audited Corrections, Raster, BOQ Editing

**Verified:** 2026-09-11 · **Verdict:** Round 6 scope met. The product now
has the full advisory AI layer (provider abstraction + guardrails,
T060/T065/T122), raster ingestion with honest candidates (T033/T048), the
review workspace depth the frozen contracts prescribe (T114 →
T072/T073/T075), and BOQ editing endpoints with recompute-diff and a
validation report (T086/T093). The trust doctrine held on every new
surface — most importantly, **the AI layer still cannot write a quantity**:
it writes only `ai_suggestions` rows (advisory, human-gated), and the
E2E now proves in the browser that the ONLY way a quantity changes is the
audited human correction, visible beside its struck-through original,
flowing into the BOQ.

## The headline invariants this round added

1. **The durable measurement identity is per-run, and ambiguity is a
   409, never a guess.** The resolver accepts the globally-unique row id
   or the durable `measurement_id`; when a second run of the same drawing
   makes the identity match two runs' rows, the request is refused with
   `ambiguous_measurement_identity` naming the disambiguator — never a
   `MultipleResultsFound` 500, never first-by-order. The UI always sends
   the row id (globally unique PK). Pinned by a dedicated regression test
   reproducing the exact E2E dev-DB failure mode.
2. **A correction never mutates the original value.** The human's number
   lands in `corrected_value` on the SAME row; the original stays visible
   (struck through in the UI); the audit row carries both sides; a BOQ
   built after the correction bills the corrected number with
   `human_correction` provenance. This also closed a latent R5-era gap:
   the BOQ builder's persisted-measurement wrapper billed the stale
   engine `value` even when a `corrected_value` existed — now it bills the
   correction, pinned by test.
3. **Every project-scoped audit row carries `project_id`.** The Round 5
   audit writers (BOQ transitions, build, export, scale confirm — THE
   human gate) predates the column and silently wrote NULL, hiding
   approve/submit/export/confirm-scale from the project trail the Round 6
   audit tab renders. All writers now scope honestly (catalogue
   SET_RATE stays workspace-global — that is its honest scope). Audit
   before/after also became honest: the pre-transition status is
   captured before the machine hop, never the post-state on both sides.
4. **Raster is a proposal surface, never a measurement surface.**
   `parse_raster` (Pillow-only) produces zero geometries by doctrine; the
   OpenCV candidates are pixel-space-only with "(verify)" labels and
   confidence capped at 0.75 at construction; px→mm conversion is
   deliberately absent (needs a confirmed scale — the human gate).
   Nothing measurable can happen from a raster upload.
5. **AI suggestions are advisory rows, clamped and stripped.** Provider
   payloads validate against a strict `SuggestionSchema`
   (`additionalProperties: false`); `sanitize` strips 12 quantity-field
   aliases at any nesting depth (an AI payload can never smuggle a
   number into a measurement field); confidence is refused outside
   [0, 0.95] at construction and re-clamped server-side; every call is
   logged in `prompt_logs` (provider, model, kind, prompt, response).
   The stub provider returns confidence 0.05 — visibly, honestly, not
   smart.

## What was built

### AI provider abstraction + guardrails (Worker A, T060/T065/T122)

`classification/provider.py` — `AiProvider` Protocol,
`SuggestionSchema` (pydantic, strict), `validate_payload` with
machine-readable failure codes (undeclared_field / missing_required /
type_mismatch), `ProviderResult` with `provider` provenance.
`classification/http_provider.py` — sync httpx (MockTransport seam),
>1MiB payload refused. `classification/stub_provider.py` — kind-aware
refusals, confidence 0.05. `classification/sanitize.py` — the 12-alias
quantity stripper (any depth), size caps (4KB / 512-char strings /
nesting depth), no JSON re-parse inside strings. 51 tests in
`tests/unit/test_ai_guardrails.py`.

### Raster ingestion + candidates (Worker A, T033/T048)

`ingestion/raster/__init__.py` — `parse_raster`: Pillow-only, 50MP
decompression-bomb guard via header pre-read, `sheet_ref "image:1"`,
`geometries=()` and `text_tokens=()` always (no auto-measurement),
no scale proposal. `ingestion/raster/candidates.py` — OpenCV
walls/rooms candidate detection, pixel-space only, "(verify)" labels,
confidence ≤ 0.75. Six byte-stable generated fixtures (PNG/JPEG/WebP/GIF
incl. one above the wall-threshold and one below). 18 + 28 tests.
CI installs the new `cv` extra (opencv-python-headless).

### Review workspace depth (Worker B, T072/T073/T075)

`backend/app/services/review_service.py` —
`review_measurement` (accept / correct; two validated hops through the
NEEDS_REVIEW pivot — MEASURED_ZERO→MEASURED has no direct edge by
design; BOQ-past-DRAFT guard with `reapprove_first`); `override_classification`
(type_source=human_set, AI provenance preserved); ownership-scoped
`resolve_measurement` (invariant 1). `backend/app/api/review.py` —
`POST /measurements/{ref}/review`, `POST /elements/{id}/classification`,
`GET /projects/{pid}/audit` (subject_type|actor|since|before|limit
filters, deterministic DESC/id-DESC order, before-cursor pagination,
pydantic-validated boundary inputs — the asyncpg cast trap never reaches
SQL). Frontend: `RunsTab` Accept/Correct inline forms (struck-through
original + corrected pill), element type override panel, `AuditTab`
with subject-type filter, `reviewHelpers.ts` pure helpers, 63 vitest
total (17 new), 35 review-action backend tests (34 + the ambiguity
regression).

### BOQ editing + analyze wiring (Lead, T086/T093 + T060 backend half)

`backend/app/services/boq_service.py` — DRAFT-only section CRUD
(add/update/delete, audited with project_id), manual items (origin
"manual", priced via the money kernel), item update with
`mapped_quantity_immutable` refusal (correct the measurement instead),
delete item, `recompute_boq` (bills `corrected_value` when present,
per-item before/after diff, idempotent), `validation_report` (T093:
no_items / unpriced_item / broken_mapping / run blockers /
ready_for_approval). `backend/app/api/boqs.py` + `runs.py` — the frozen
contract's section/item/recompute/validation routes;
`POST /runs/{id}/analyze` (202 job, stub provider default) +
`GET /runs/{id}/ai/insights` (reads the advisory rows only).
`ai_service.execute_analyze` refuses non-completed runs, invalidates
prior suggestions by replace, logs prompts, clamps confidence.
8 editing tests + 2 analyze-wiring tests.

### E2E — the correction journey, in the browser

`frontend/e2e/gated-journey.spec.ts` extended: correct Wall 1's length
through the UI (rows addressed by label, never position), assert the
struck-through original beside the corrected pill, assert the BOQ
bills the corrected sum, resolve the R5 unmapped blockers, approve,
export, then the audit tab shows `correct_quantity` and `approve` —
the human's fingerprint on every number that changed.

## Latent defects caught this round (all closed with tests)

- **The ambiguity 500 (invariant 1's origin).** The E2E dev DB holds
  multiple journeys' runs of wall_plan.dxf → identical digest-derived
  identities → `MultipleResultsFound` 500 on review. Root-caused through
  the API log, fixed with the ownership-scoped resolver + 409, pinned
  by the two-runs regression test.
- **The R5 BOQ builder billed the stale engine value** after a
  correction (fresh builds ignored `corrected_value`). Fixed in the
  persisted-measurement wrapper, pinned by
  `fresh-build-bills-corrected-value`.
- **Audit rows invisible on the project trail** (invariant 3): the E2E
  audit-tab assertion is what surfaced it — `approve` rows carried NULL
  project_id, so the project-scoped list filtered them out.
- **multiply_rate signature misuse in the editing PR**: the first draft
  called it without currency and index a Money object; replaced by the
  `_line_total` helper over the money kernel.
- **Recompute queried row ids against the identity column** (the same
  per-run-identity trap, caught by test before it could ship).

## Verification actually executed

| Check | Result |
|---|---|
| `.venv/bin/pytest backend/tests` | **136 passed** (live PostgreSQL) |
| `.venv/bin/pytest tests/unit tests/integration core/tests` | **439 passed** |
| Browser E2E (api :8099 + worker + vite + Postgres) | **1 passed** — correction → strikethrough → corrected BOQ → blockers → approve → export → audit |
| Strict mypy (CI package list, 98 files) | clean |
| ruff check . | clean |
| lint-imports | **9 kept, 0 broken** (incl. the new AI boundary) |
| Reference-leak guard | clean |
| Frontend `npm run test` / `tsc` / `build` | **63 passed** / clean / clean |
| Migration roundtrip | up/down/up verified; applied to dev DB |

## Parallel agents (orchestration per constraint)

Two worker agents (limit 3), non-overlapping ownership, Lead owned
integration and verified both slices personally before accepting them:

- **Worker A — AI + raster**: provider abstraction, guardrails, sanitize,
  raster parser + candidates + fixtures. 97 tests.
- **Worker B — review actions**: corrections, overrides, audit API,
  frontend review surfaces. 34 backend + 17 frontend tests.
- Honest deviations accepted at review: ProviderResult's 5th `provider`
  field (matches the Lead's service wiring); stub confidence 0.05
  (inside the clamp, honestly low); GIF fixture resized so its edges
  exceed the declared wall threshold (a fixture below its own threshold
  would be a fake pin).

## Known limitations (honest register)

- The provider registry is stub + http; a real vision provider is a
  config change (`ai_provider=http`, base_url, key, model) — no vendor
  lock-in, no vendor code.
- AI suggestions are created and surfaced but no suggestion-apply UI
  exists yet (applying is itself an audited human action — T-something
  later; the API for it does not exist and was not invented).
- Raster uploads parse honestly but produce nothing measurable (by
  doctrine); a raster evidence/viewer surface is later work.
- BOQ editing is DRAFT-only; the reject→draft re-entry is the only way
  back past review (by design).
- Run history remains session-local in the UI; list-runs / list-exports
  endpoints are still later tickets.
- CI does not yet run the browser E2E job (service composition; the
  Makefile target documents the local invocation).

## Round 7 candidates (from the backlog, not committed to)

xlsx/pdf export (T101/T102), list-runs/list-exports endpoints, catalog
management UI (T084's mapping UI), E2E-in-CI composition, suggestion
apply (audited) flow.
