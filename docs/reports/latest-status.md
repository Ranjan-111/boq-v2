# Latest Status

**Verified:** 2026-09-10 · **HEAD:** Round 4 complete locally (commits
`3f65938..` through the Round 4 series; see round-4-report.md) — push +
remote CI run pending at the time of this writing.
**Current:** Round 4 — **the product slice is COMPLETE and locally verified
end to end**, including the browser E2E of the gated journey.

Rounds 1–3 remain completed historical milestones. Round 4 turned the
verified trust boundary into a usable product: upload → parse → human scale
confirmation → measurement run → BOQ build → submit/review/approve →
approval-gated CSV export, all through the UI, with every trust refusal
demonstrated in the browser.

## This checkpoint (Round 4)

- **Upstream API (T019/T020):** validate-before-store upload with
  magic-byte sniffing, parse jobs on the SKIP-LOCKED queue, sheets with
  PROPOSED calibrations only, `POST /sheets/{id}/scale/confirm` as the ONLY
  CONFIRMED writer (audited), catalog with integer-minor rates.
- **Run persistence:** stored-bytes re-parse, persisted CONFIRMED
  calibration, measurements with durable `measurement_id` identities,
  elements + geometry + evidence links + exceptions; unconfirmed scale is a
  BLOCKING exception with zero measurements — never a guess.
- **BOQ gates, server-side:** build from MEASURED-only rows (unmapped units
  reported as blockers), submit → review → approve state machine enforced
  with audited transitions; export builds the `ExportApproval` scope from
  trusted persistence; artifacts content-addressed with sha256.
- **Frontend:** the four workspace tabs are live (upload/parse polling,
  inline human scale confirmation, run form that offers ONLY confirmed-scale
  sheets, measurement table with per-row state badges, click-to-highlight
  evidence in a pan/zoom SVG viewer, exceptions with inline resolve, BOQ
  workspace with 409 blocker surfacing, export card with sha + download).
- **Browser E2E (T126):** Playwright journey register → … → CSV download,
  green (8.6s), including the pre-gate refusal (Start run disabled until a
  human confirms scale).
- **Six latent defects the journey exposed were closed with regressions**
  (queue.fail param bug, `-m` worker registry split, honest browser mimes
  rejected, asyncpg UUID/str crashes, a store-mirror render loop, a
  reviewed-state dead end) — see round-4-report.md.

## Verified checks (exact)

| Check | Result |
|---|---|
| pytest (core/tests + backend/tests + tests, live PG) | **344 passed** |
| Browser E2E | **1 passed** (8.6s) |
| mypy strict (CI list) | 84 files clean |
| ruff check . | clean |
| lint-imports | 9 kept, 0 broken |
| reference-leak guard | clean (151 files) |
| frontend vitest | 46 passed |
| frontend build (tsc strict) | clean |
| clean-worktree CI reproduction (fresh install) | all of the above green |

## Next up (Round 5 candidates — from the backlog, not committed to)

Review workspace depth (T114), BOQ editing endpoints, xlsx/pdf export
(T101/T102), list-runs/list-exports endpoints, catalog management UI, sheet
tiles, deploy hardening (T128). The E2E-in-CI composition is also open (needs
the api+worker+vite service set in a CI job).
