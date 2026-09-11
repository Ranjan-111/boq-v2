# Latest Status

**Verified:** 2026-09-11 · **HEAD:** Round 6 complete locally (the Round 6
commit series; see round-6-report.md).
**Current:** Round 6 — **the advisory AI layer, audited corrections, raster
ingestion, and BOQ editing are COMPLETE and locally verified**. The trust
doctrine held on every new surface: the AI writes only advisory
`ai_suggestions` rows (never a quantity), and the E2E proves in the
browser that the only way a number changes is the audited human
correction — original struck through beside it, flowing into the BOQ.

Rounds 1–5 remain completed historical milestones. Round 6 added the
human-decision surfaces the frozen contracts prescribed (T114 →
T072/T073/T075, T086/T093) and the AI/raster halves (T033/T048/T060/T065),
closing the R5 register's deferred items.

## This checkpoint (Round 6)

- **AI provider abstraction + guardrails (T060/T065/T122):** AiProvider
  Protocol, strict SuggestionSchema validation, the 12-alias quantity
  stripper (any nesting depth), confidence refused outside [0, 0.95]
  and re-clamped server-side, stub provider (0.05 — honestly low),
  sync http provider, prompt_logs for every call. New import-linter
  contract: classification never reaches engines/takeoff/ingestion/boq.
- **Raster ingestion + candidates (T033/T048):** Pillow-only parser,
  50MP guard, geometries=() ALWAYS (no auto-measurement from raster),
  no scale proposal; OpenCV candidates pixel-space-only, "(verify)"
  labels, confidence ≤ 0.75; px→mm deliberately absent (needs confirmed
  scale — the human gate).
- **Review workspace depth (T072/T073/T075):** audited accept/correct
  (value column NEVER mutated — corrected_value on the same row, two
  validated hops through NEEDS_REVIEW), classification override
  (human_set, AI provenance preserved), project audit trail endpoint +
  AuditTab with filters. The durable identity is per-run: ambiguity is
  a 409 naming the row id — never a 500, never first-by-order.
- **BOQ editing (T086/T093):** DRAFT-only section/item CRUD, manual
  items priced via the money kernel, mapped-quantity PATCH refused
  (correct the measurement instead), recompute with per-item
  before/after diff (bills corrected_value; idempotent), validation
  report (no_items/unpriced/broken_mapping/blockers → ready flag).
- **Analyze wiring (T060 backend):** POST /runs/{id}/analyze (202 job,
  stub default) + GET /runs/{id}/ai/insights; refuses non-completed
  runs; suggestions advisory-only with rejected_fields stamps.

## Verified checks (exact)

| Check | Result |
|---|---|
| pytest backend/tests (live PG) | **136 passed** |
| pytest tests/unit + tests/integration + core/tests | **439 passed** |
| Browser E2E (api :8099 + worker + vite + Postgres) | **1 passed** — correction → strikethrough → corrected BOQ sum → blockers → approve → export → audit |
| mypy strict (CI list) | 98 files clean |
| ruff check . | clean |
| lint-imports | 9 kept, 0 broken |
| reference-leak guard | clean |
| frontend vitest | 63 passed |
| frontend build (tsc strict) | clean |
| migration roundtrip (prompt_logs + audit.project_id) | up/down/up verified |

## Next up (Round 7 candidates — from the backlog, not committed to)

xlsx/pdf export (T101/T102), list-runs/list-exports endpoints, catalog
management UI (T084 mapping UI), suggestion-apply (audited) flow, raster
evidence/viewer surface, E2E-in-CI composition.
