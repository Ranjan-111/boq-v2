# Tool Availability — Current Checkpoint

**Verified:** 2026-09-10 (Round 4 product slice) · **Environment:**
local macOS workspace.
Historical routing/classifier errors below belong to earlier sessions and
must not be treated as current tool status.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; full-stack orchestration (api :8099 + worker + Vite dev server + Postgres) driven for E2E; clean-worktree CI reproduction executed |
| Test isolation | Architecture guard tests copy the source tree into tmp dirs — user repo never mutated |
| Python suite | **344 passed, 0 skipped** (live PostgreSQL; +44 over Round 3); job queue now has a dedicated live-DB regression (`TestQueueFail`) after the ambiguous-param defect |
| PostgreSQL | Container `boqv2-postgres-1` healthy on 5432; dev DB migrated to head (`93865264fc81`) during the round; scratch CI DB + worktree cleaned up after verification |
| Ruff / mypy | ruff clean; mypy strict 84 files clean |
| Architecture | import-linter **9 kept, 0 broken** (unchanged — worker imports of ingestion/takeoff are unconstrained modules, verified, no pyproject change needed) |
| Subagents | **2 worker agents ran** (limit 3) — Worker 1 (upload/parse/sheets/scale/catalog API), Worker 2 (frontend flow); both delivered verified reports; integration findings folded into round-4-report.md |
| Frontend | **46 vitest passed** (was 5); tsc strict + Vite build clean; `npm ci` parity check in clean worktree passed |
| Browser E2E | **Green** — Playwright 1.63 + chromium, the gated DXF→wall→BOQ→CSV journey in 8.6s against the real backend; `make e2e` target added; not yet in remote CI (needs the api+worker+vite service composition) |
| Clean-worktree CI reproduction | Caught the undeclared `rapidfuzz` dependency before push (venv-only, fresh install would 500 on import); fresh-venv install + all gates green |
| Remote CI | Round 4 push pending at checkpoint time (local verification complete) |

Installed versions checked this round: rapidfuzz 3.14.6 (MIT;
License-Expression in METADATA), Playwright 1.63 with chromium headless
shell, ezdxf 1.4.4, Shapely 2.1.2, mypy 2.3.1, pytest 9.1.1, import-linter
2.15. Banned-package spot check clean (no PyMuPDF).

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
- Chromium sends `image/vnd.dxf` / `application/octet-stream` for `.dxf`
  uploads (no registered OS mime); the allowlist must let honest unknowns
  fall through to magic-byte sniffing, which is the check that cannot be
  lied about.
- Playwright page.evaluate needs a page origin before relative fetch works
  (land on the app first); zustand store objects must never be effect deps
  (stable action selectors + change-guarded deps instead); `<option>`
  elements are hidden in the a11y tree (assert via the combobox's selected
  text or button-disabled state, not option visibility).
- FastAPI's newer `include_router` keeps `_IncludedRouter` wrappers —
  `app.routes` no longer shows included paths; dump `app.openapi()` for
  route verification.
