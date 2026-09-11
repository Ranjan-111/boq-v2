# Tool Availability — Current Checkpoint

**Verified:** 2026-09-11 (Round 6 advisory AI + review workspace + BOQ
editing) · **Environment:** local macOS workspace.
Historical routing/classifier errors below belong to earlier sessions and
must not be treated as current tool status.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; full-stack orchestration (api :8099 + worker + Vite dev server + Postgres) driven for E2E including the correction → strikethrough → corrected-BOQ → audit flow; two API/worker restarts this round (code changed under a no-reload stack — kill + relaunch, 401 on /auth/me is the alive signal) |
| Test isolation | Architecture guard tests copy the source tree into tmp dirs — user repo never mutated |
| Python suite | **backend/tests 136 + core/unit/integration 439 = 575 passed, 0 skipped** (live PostgreSQL; +140 over Round 5: 51 AI guardrails, 18 raster parser, 28 raster candidates, 35 review actions incl. the per-run-identity ambiguity regression, 10 BOQ editing/analyze, plus R3 pins updated additively) |
| PostgreSQL | Container `boqv2-postgres-1` healthy on 5432; dev DB at head (new migration this round: `prompt_logs` table + `audit_log.project_id` column, roundtripped up/down/up) |
| Ruff / mypy | ruff clean; mypy strict 98 files clean |
| Architecture | import-linter **9 kept, 0 broken** (new AI-boundary contract: classification never reaches engines/takeoff/ingestion/boq/pricing) |
| Subagents | **2 worker agents ran** (limit 3) — Worker A (AI providers + guardrails + sanitize + raster parser/candidates/fixtures, 97 tests), Worker B (review corrections/overrides/audit API + frontend review surfaces, 34 backend + 17 frontend tests); both slices verified personally by the Lead before acceptance; integration findings folded into round-6-report.md |
| Frontend | **63 vitest passed**; tsc strict + Vite build clean; eslint not installed locally (CI matrix: tsc + vitest + build) |
| Browser E2E | **Green** — the gated journey includes the Round 6 core: UI correction with struck-through original, corrected BOQ sum, audit-tab assertions; not yet in remote CI (needs the api+worker+vite service composition) |
| Remote CI | Round 6 push pending at checkpoint time (local verification complete) |

Installed versions checked this round: opencv-python-headless 5.0.0 (Apache-2.0,
new `cv` extra — installed into .venv and the CI/Makefile install lines),
Pillow (BSD) via the raster stack, pdfplumber 0.11.10 (MIT), Playwright
1.63, ezdxf 1.4.4, Shapely 2.1.2, mypy 2.3.1, pytest 9.1.1,
import-linter 2.15. Banned-package spot check clean (PyMuPDF absent).

Tool notes worth retaining (cumulative):
- `lint-imports` CLI takes `--config`, not `-c`; `python -m importlinter`
  has no `__main__` (guard test learned this the honest way).
- External forbidden modules require top-level
  `include_external_packages = true` in `[tool.importlinter]`.
- **`python -m pkg.mod` executes the file as `__main__` — a second import of
  the same module creates a SEPARATE module instance.** This split the
  worker's handler registry in two (handlers registered into the imported
  copy while the loop read the `__main__` copy) and parse jobs died with
  `unknown_kind`. The `__main__` block now delegates to the canonical
  module. Bit the Round 4 integration; the fix is in worker.py with a
  comment.
- **asyncpg returns pgproto UUID objects for `Uuid` columns even when the
  ORM types them `Mapped[str]`** — any `uuid.UUID(x)` on a caller-provided
  id from a SELECTed row crashes, and UUIDs must never go into JSONB job
  payloads. Normalize at the boundary (see `_as_uuid` in boq_service).
- **The durable `measurement_id` (uuid5 of inputs_digest) is unique PER
  RUN, never globally.** Any lookup that resolves it globally will find
  0, 1, or MANY rows depending on how many runs of the same drawing the
  user has — the E2E dev DB (multiple journeys, same fixture) surfaced
  this as a MultipleResultsFound 500. The honest resolver scopes to the
  requester's runs and refuses true ambiguity with a named 409
  (`ambiguous_measurement_identity`); the UI always sends the row id
  (globally unique PK). Bit the review surface this round; recompute had
  the same trap (fixed against the identity column scoped by run_id).
- **SQLAlchemy asyncpg batched INSERTs with RETURNING break when str ids
  are passed for `Uuid` columns and >1 row is flushed together**
  (insertmanyvalues sentinel matching compares the str params against
  pgproto UUID returns → `KeyError` → `InvalidRequestError`). The codebase
  idiom is add→flush PER ROW; batched flushes of hand-built Uuid-id rows
  must be split. Bit the unmapped-exception inserts in R5.
- Chromium sends `image/vnd.dxf` / `application/octet-stream` for `.dxf`
  uploads (no registered OS mime); the allowlist must let honest unknowns
  fall through to magic-byte sniffing, which is the check that cannot be
  lied about.
- Playwright page.evaluate needs a page origin before relative fetch works
  (land on the app first); zustand store objects must never be effect deps
  (stable action selectors + change-guarded deps instead); `<option>`
  elements are hidden in the a11y tree (assert via the combobox's selected
  text or button-disabled state, not option visibility); **measurement
  rows order by UUID — address them by label via
  `getByRole("row").filter({ has: cell })`, never by position**; a
  `getByText` that matches a form label will find the label, not the
  payload-rendered value — assert on value-bearing elements.
- FastAPI's newer `include_router` keeps `_IncludedRouter` wrappers —
  `app.routes` no longer shows included paths; dump `app.openapi()` for
  route verification.
- A no-reload uvicorn stack serves pre-fix code — after editing services,
  kill and relaunch before rerunning E2E (a passing test against a stale
  process verifies nothing).
