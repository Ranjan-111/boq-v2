# Round 4 Report — The Product Slice: Upload → Wall → BOQ → CSV, End to End

**Verified:** 2026-09-10 · **HEAD:** `ab26e22` · **Verdict:** Round 4 exit
criterion met. The Round 3 trust boundary is now a usable product: a real
browser session can upload a DXF, parse it to a modelspace sheet, confirm
scale through the human gate, run a measurement, build a BOQ from the
measured quantities, walk it through submit → review → approve, and export a
CSV with a content-addressed digest — with every refusal the trust doctrine
promises demonstrated in the UI along the way. All gates green; verified
again in a clean worktree with a fresh dependency install.

## The journey, proven twice

The same gated path is now covered at two levels:

1. **Backend service journey** (`backend/tests/test_boq_journey.py`, live
   PostgreSQL): unconfirmed scale → BLOCKING exception + zero measurements +
   `completed_with_exceptions`; confirmed scale → 4 measured rows with durable
   `measurement_id` identities; BOQ gates refuse in each illegal state
   (draft-approve, in-review-approve) and with unresolved blockers until
   resolved; export builds the `ExportApproval` scope from trusted
   persistence; byte-identical re-export; every transition audited.
2. **Browser E2E** (`frontend/e2e/gated-journey.spec.ts`, Playwright 1.63
   against api :8099 + worker + Postgres + Vite dev server): the full journey
   through the real UI, including the pre-gate refusal — the run form offers
   **no measurable sheet** and Start run stays disabled until a human
   confirms scale; parse writes a PROPOSED calibration (badge: "scale
   proposed — needs confirmation"), never CONFIRMED. Green in 8.6s.

## What was built (8 commits 7fd4041..ab26e22)

### Foundation (7fd4041)
`MeasurementRecord.element_index` + `RunOutput.elements` — engine output now
binds each measurement to its element additively (element_index deliberately
excluded from the replay digest; determinism pins untouched, 3 new tests).
Migration `93865264fc81`: `drawing_sheets.sheet_ref` (backfilled from
page_number, unique widened), `drawing_files.parse_warnings` JSONB,
`measurements.measurement_id` (uuid5 identity, unique per run) + `label`,
`export_artifacts.status`. Roundtripped up/down/up on scratch PG.

### Lead slice (a438cb3)
`run_service.execute_run` — re-parses STORED bytes, loads the persisted
CONFIRMED calibration, persists Element/Geometry/Measurement/EvidenceLink/
Exception rows, transitions through the run state machine at a single site.
Unconfirmed scale is an honest BLOCKING exception with zero measurements —
never a guess. `boq_service` — build from run (MEASURED-only, unmapped units
reported as blockers, never silently dropped), submit/review/approve/reject
gates server-side, export builds `ExportApproval` from persistence and
re-validates on serialize. Runs/review/boqs routers; job handlers for
parse/run/export kinds.

### Upstream slice (c90ada3, Worker 1)
Upload (validate-before-store, magic-byte-first sniff, dedupe, content-
addressed storage), parse service (sheets + PROPOSED calibrations only — a
repo-wide test asserts no CONFIRMED row exists without the human POST),
`POST /sheets/{id}/scale/confirm` as the ONLY CONFIRMED writer (audited),
catalog (region-scoped items, integer-minor rates, rapidfuzz search), job
status reads. 34 new backend tests.

### Frontend slice (1205860, Worker 2)
Workspace tabs go live: Drawings (upload → parse polling → PROPOSED badges →
inline human confirmation), Runs (confirmed-scale-only sheet select,
measurement table with per-row state badges, click-to-highlight evidence in
the pan/zoom SVG viewer, exceptions with inline resolve), BOQ (build →
submit → complete review → approve/reject with 409 blocker surfacing →
export CSV with sha + download), Exports (session history). 40 new vitest
(pure-function style under the no-new-deps constraint), tsc strict.

### Integration + E2E (5204dc8, 41d8494, ab26e22, Lead)
Router registration (30 routes, OpenAPI-verified). `POST /boqs/{id}/review`
(IN_REVIEW → REVIEWED, audited) — the hop the approval machine requires.
Contract alignment: unscoped run reads with creator ownership, evidence by
durable `measurement_id`, nested sheet calibration objects, `parse_warnings`
arrays. **Three latent defects found and closed with regressions** (details
below). Playwright infra + `make e2e` + `rapidfuzz>=3.9` declared in
pyproject (it was venv-only; fresh CI installs would have failed on import).

## Latent defects the E2E journey exposed (all closed with regression tests)

1. **`queue.fail` ambiguous bind param** — one `:s` reused across a varchar
   assignment and a text comparison; asyncpg rejects every call with
   `AmbiguousParameterError`. No test had ever called `fail()`. Fixed (per-
   usage-site params) + `TestQueueFail` regression (terminal and retryable).
2. **`python -m` double-module worker registry** — running the worker via
   `-m` executes the file as `__main__`; `handlers.py`'s
   `from backend.app.jobs import worker` then registers into a *separate*
   module instance, leaving the running loop's `_HANDLERS` empty: every
   parse job died `unknown_kind`. The `__main__` block now delegates to the
   canonical module. Verified live: parse jobs succeed.
3. **Honest browser mimes rejected** — Chromium sends `image/vnd.dxf` (and
   generic `application/octet-stream`) for `.dxf`; the mime allowlist refused
   real DXF uploads. Both now fall through to the magic-byte sniff, which
   remains the check that cannot be lied about (fake-DXF bytes still
   rejected — tested). Two regression tests.
4. **Asyncpg UUID/str typing crashes** — `uuid.UUID(actor)` on an
   already-native pgproto UUID (500 on BOQ submit) and a UUID inside the
   export job payload (`Object of type UUID is not JSON serializable`,
   500 on export). Both normalized with regressions via the journey suite.
5. **BoqTab infinite re-render** — a store-mirror effect depended on the
   whole zustand store object while patching with new-objects-every-time:
   Maximum update depth exceeded. Fixed with stable action selectors +
   change-guarded deps (the E2E surfaced it; no unit test could have).
6. **Reviewed-state dead end** — BoqActions rendered buttons only for
   draft/in_review; a REVIEWED BOQ had no Approve button despite the gate
   requiring exactly that state. Fixed (Approve/reject also render for
   `reviewed`; Complete review only for `in_review`).

Also fixed at integration: RunsTab's sheet options cached under a composite
query key the scale-confirm never invalidated (prefix invalidation added);
the upload input gained `aria-label` (E2E + a11y); dedupe-reuse `job_id:""`
skips polling instead of polling a 404.

## Verification actually executed

| Check | Result |
|---|---|
| `.venv/bin/pytest core/tests backend/tests tests` | **344 passed, 0 skipped** (live PostgreSQL) |
| Browser E2E (`make e2e` path: api :8099 + worker + vite) | **1 passed** (8.6s) |
| Strict mypy (CI package list) | **84 files clean** |
| ruff check . | clean |
| lint-imports | **9 kept, 0 broken** |
| Reference-leak guard | clean (151 tracked files) |
| Banned-package check (CI parity) | clean (no PyMuPDF) |
| Frontend `npm run test` | **46 passed** |
| Frontend `npm run build` | tsc strict + Vite clean |
| git diff --check | clean |
| **Clean-worktree CI reproduction** (fresh venv, `uv pip install -e .[dev,ingest,geo]`, untracked files copied, context.md excluded) | ruff/mypy/lint-imports/guard **all clean**; pytest **342 passed** on a scratch `boq_test_ci` DB; `npm ci` + build + vitest 46 passed; scratch DB and worktree removed after |

Round 3 trust suites (engine determinism pins, trust-hardening, wall
detection, assembly, DXF parser) all still green — the invariants survived
the product build on top of them.

## Parallel agents (orchestration per constraint)

Two worker agents (limit 3), non-overlapping ownership, Lead owned
integration:

- **Worker 1 — upstream API**: 9 files, 34 tests. Deviations were honest
  and correct (idempotency keys with attempt ordinals because
  `jobs.idempotency_key` is UNIQUE across history; dedupe-reuse returns
  `job_id: ""`; DELETE guards content-addressed blobs by reference count).
  Their report flagged `queue.fail` and the undeclared rapidfuzz — both
  confirmed and fixed at integration.
- **Worker 2 — frontend flow**: 23 files, 40 vitest. Deviations honest
  (session-scoped run/export pickers labeled as such because no list
  endpoints exist in the v1 contract; pure-function tests because
  @testing-library is not a dependency; viewer geometry from per-measurement
  evidence only — no tiles endpoint this round).

## Known limitations (honest register)

- Run history and export history are session-local in the UI (no list
  endpoints in the v1 contract; labeled in the UI, not hidden).
- Catalog UI is API-only this round (typed client methods exist; a
  management surface is a later ticket).
- The viewer renders evidence geometry per selected measurement (no sheet
  tiles/z-x-y endpoint this round).
- Two-point scale confirmation persists no points in V1 (the factor is the
  input; method recorded).
- Dev DB note: `make api`/worker against a pre-R4 database requires
  `make db-migrate` first (the dev `boq` DB was migrated during this round).
- One JWT InsecureKeyLengthWarning (23-byte test key) — test fixture, not
  a product defect (carried from Round 3).
- CI does not yet run the browser E2E job (it needs the api+worker+vite
  service composition; the Makefile target documents the local invocation).

## Round 5 candidates (from the backlog, not committed to)

Review workspace depth (T114: corrections with provenance, overrides, audit
view), BOQ editing (sections/items/recompute/validation endpoints),
xlsx/pdf export (T101/T102), list-runs/list-exports endpoints, catalog
management UI, sheet tiles for full-drawing viewer context (T070+), deploy
hardening (T128).
