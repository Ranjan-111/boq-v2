# Tool Availability — Current Checkpoint

**Verified:** 2026-09-11 (Round 5 full takeoff engine) · **Environment:**
local macOS workspace.
Historical routing/classifier errors below belong to earlier sessions and
must not be treated as current tool status.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; full-stack orchestration (api :8099 + worker + Vite dev server + Postgres) driven for E2E including the new unmapped-blocker resolution flow |
| Test isolation | Architecture guard tests copy the source tree into tmp dirs — user repo never mutated |
| Python suite | **435 passed, 0 skipped** (live PostgreSQL; +91 over Round 4: 21 full-engine, 20 DXF text/block, 51 PDF parser/candidates, minus R3 pins updated to the R5 measurement contract) |
| PostgreSQL | Container `boqv2-postgres-1` healthy on 5432; dev DB at head (no new migrations this round — engine output widened additively; unmapped blockers reuse the exceptions table) |
| Ruff / mypy | ruff clean; mypy strict 83 files clean |
| Architecture | import-linter **9 kept, 0 broken** (unchanged — new modules respect the same contracts) |
| Subagents | **2 worker agents ran** (limit 3) — Worker A (PDF parser + candidates + fixtures, 51 tests), Worker B (DXF text tokens + opening-block names, 20 tests); both delivered verified reports; integration findings folded into round-5-report.md |
| Frontend | **46 vitest passed**; tsc strict + Vite build clean; E2E spec extended with the Round 5 unmapped-resolution flow |
| Browser E2E | **Green** — the gated journey in 8.8s against the real backend, now including approve-refused-with-blockers → audited resolve → approve; not yet in remote CI (needs the api+worker+vite service composition) |
| Remote CI | Round 5 push pending at checkpoint time (local verification complete) |

Installed versions checked this round: pdfplumber 0.11.10 (MIT) — the
PDF stack landed this round (pypdf + pypdfium2 already present for
metadata/tiles; PyMuPDF remains banned and absent), Playwright 1.63,
ezdxf 1.4.4, Shapely 2.1.2, mypy 2.3.1, pytest 9.1.1, import-linter 2.15.
Banned-package spot check clean.

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
- **SQLAlchemy asyncpg batched INSERTs with RETURNING break when str ids
  are passed for `Uuid` columns and >1 row is flushed together**
  (insertmanyvalues sentinel matching compares the str params against
  pgproto UUID returns → `KeyError` → `InvalidRequestError`). The codebase
  idiom is add→flush PER ROW; batched flushes of hand-built Uuid-id rows
  must be split. Bit the unmapped-exception inserts this round.
