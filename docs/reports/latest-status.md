# Latest Status

**Verified:** 2026-09-11 · **HEAD:** Round 5 complete locally (the Round 5
commit series; see round-5-report.md).
**Current:** Round 5 — **the full takeoff engine is COMPLETE and locally
verified**: rooms, floors, openings, deductions on DXF + PDF, with the
trust doctrine enforced on every new surface and the R4 unmapped gap
closed (unmapped measurements now BLOCK approval/export server-side
until a human resolves them).

Rounds 1–4 remain completed historical milestones. Round 5 turned the
single-element engine (walls) into the full deterministic takeoff: every
element kind the roadmap defines for this round measures through the same
gated pipeline, with the two belief-breaking false positives (phantom
openings between separate buildings; gross+net billed as one line)
caught and refused before they could reach a BOQ.

## This checkpoint (Round 5)

- **Rooms + floors (T043/T044):** centerline polygonization with honest
  absence vs refusal splits (<3 walls = no room, no exception; ≥3 walls
  unclosed = `room_not_enclosed` REVIEW; topology errors BLOCKING),
  container/contained annulus handling, TEXT-token labels strictly
  inside faces, gross/net per room + floor roll-up.
- **Openings + deductions (T045/T046):** named-block detection (one
  placed block = one opening; local-frame hosting) + the corroboration
  doctrine — **bare collinear gaps are never openings**, only gaps with
  a door/window-named block in the span count; bare gaps surface as
  `opening_ambiguous` REVIEW. Net wall area subtracts geometrically
  from rule inputs (replay-honest); zero openings = MEASURED_ZERO with
  evidence.
- **PDF ingestion (T032/T034/T047):** pdfplumber parser with the same
  ParseResult contract, text tokens, honest refusals (corrupt/no-Root/
  encrypted-locked), PROPOSED-only scale proposals from `1:N` text;
  candidate detectors (areas/lengths/count-by-example) deliberately
  not registered as rules. Backend run/parse paths dispatch on format.
- **BOQ determinism (the R4 gap closed):** mapping groups by
  (rule_id, unit); catalogue collisions settle SYMMETRICALLY (all
  claimants blocked — never first-by-order); unmapped groups persist as
  BLOCKING `unmapped_measurement` exception rows, so approve/export
  refuse until a human resolves them through the audited endpoint. The
  browser E2E now walks this resolution flow.
- **ENGINE_VERSION 0.3.1 → 0.4.0** (new rules change the measured
  vocabulary; determinism pins re-verified byte-identical).

## Verified checks (exact)

| Check | Result |
|---|---|
| pytest (tests/unit + tests/integration + core/tests + backend/tests, live PG) | **435 passed** |
| Browser E2E (api :8099 + worker + vite + Postgres) | **1 passed** (8.8s) |
| mypy strict (CI list) | 83 files clean |
| ruff check . | clean |
| lint-imports | 9 kept, 0 broken |
| reference-leak guard | clean (180 files) |
| frontend vitest | 46 passed |
| frontend build (tsc strict) | clean |
| fixtures | byte-stable; 5 new DXF + 5 new PDF only |

## Next up (Round 6 candidates — from the backlog, not committed to)

Raster ingestion + AI-vision candidates (T033/T048 — the deferred half of
Round 5, blocked on the AI layer), AI provider abstraction + guardrails
(T060/T065), review workspace depth (T114), BOQ editing endpoints
(T086), xlsx/pdf export (T101/T102), list-runs/list-exports endpoints,
catalog management UI, sheet tiles. The E2E-in-CI composition remains
open.
