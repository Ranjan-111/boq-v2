# Round 5 Report — The Full Takeoff Engine: Rooms, Floors, Openings, Deductions, PDF

**Verified:** 2026-09-11 · **Verdict:** Round 5 scope met. The takeoff
engine now measures every deterministic element kind the roadmap defines
for this round — rooms (T043), floors (T044), openings (T045),
deductions (T046) — on both ingest formats (DXF + PDF T032/T034/T047),
with the trust doctrine enforced on every new surface and two
belief-breaking false positives caught and refused before they could
feed the BOQ. Raster candidates (T033/T048) are explicitly deferred to
Round 6 (AI-vision-dependent; the roadmap marks them "NO auto-measurement"
— pulling them in without the AI layer would have meant either fake
quantities or an empty shell).

## The headline invariants this round added

1. **A bare collinear gap between walls is never an opening.**
   `multi_storey_hint.dxf` places two separate buildings' walls collinearly
   with a 7000 mm gap. The first opening-detection draft counted those gaps
   as openings — a believable-but-wrong quantity exactly of the kind the
   trust doctrine exists to prevent (two separate buildings is equally
   plausible; a phantom opening would have fed the BOQ a deduction for a
   door that is not drawn). The landed doctrine: **only gaps CORROBORATED
   by a door/window-named block in the gap span count**; bare gaps surface
   as `opening_ambiguous` (REVIEW) with the span named in the message.
2. **Gross and net can never be billed as one line.** BOQ mapping now
   groups by `(rule_id, unit)` — never bare unit — and settles catalogue
   collisions **symmetrically**: two rule groups claiming the same item
   (wall footprint m² AND wall net m² finding the one m² item) block ALL
   claimants. An inline "first group wins" guard would have been exactly
   the first-by-order pick the doctrine forbids — footprint billing only
   because "f" sorts before "n".
3. **Unmapped measurements now actually block.** Round 4 surfaced them in
   the build response but nothing server-side refused approval (the R4
   report's gap register). Round 5 persists each unmapped group as a
   BLOCKING `unmapped_measurement` exception row; approve/export refuse
   until a human resolves. The E2E journey now walks this: approve →
   refused with the unmapped blockers in the UI → human resolves through
   the audited `POST /exceptions/{id}/resolve` → approve passes.
4. **Zero openings is an honest MEASURED_ZERO row with evidence**, never
   a silent absence — and it counts SLOT geometries, not wall faces (the
   count-of-faces draft returned 2 for zero openings: a believable but
   wrong number caught in review).
5. **Replay honesty extends to the new rules.** `wall.net.area.v1` takes
   `[footprint, *slots]` and subtracts GEOMETRICALLY (shapely
   intersection), so every net value is re-derivable from its rule inputs
   alone — no deduction hidden in constants. Face-gap slots that lie
   outside the host footprint deduct zero via the same geometric
   subtraction — no special-casing, no double-count.

## What was built

### Rooms + floors (T043/T044, Lead)

`takeoff/room_detection.py` — shapely `unary_union` + `polygonize` over
wall centerlines: <3 walls is honest absence (no exception), ≥3 walls
with no closed face is `room_not_enclosed` (REVIEW), topology errors
(self-touching rings, degenerate polygons) are BLOCKING refusals.
Container/contained faces are handled by area-descending sort + `covers`
(the annulus case); net = gross minus wall footprints. Labels come from
TEXT tokens strictly inside the face with a deterministic
`(distance, text, handle)` tiebreak — a token outside a room never
labels it. `room.gross/net.area.v1` (one ring input), `floor.gross/net.area.v1`
(sum of ring inputs), one FLOOR_FINISH element + roll-up per sheet.

Hand-verified math: room_plan.dxf gross 12.000000 m² (6000×2000 mm to
centerline), net 10.640000 (minus four 0.2 m wall-footprint intrusions);
two_room_plan floor roll-up 18.000000/15.680000 — engine output matches
exactly.

### Openings + deductions (T045/T046, Lead)

`takeoff/openings.py` — named-block openings group block members by
INSERT handle (one placed block = one opening unit; per-member bboxes are
degenerate) and host blocks onto walls via local-frame lon/perp extents
(width/height swap for vertical walls fixed by computing extents in each
wall's own frame; straddling blocks via signed-distance perpendicular
bands). Face-gap openings consume the same block placements as
corroborators (invariant 1). `takeoff/deductions.py` rolls per-wall
gross/opening-count/opening-area/net and floor totals; every deduction
is geometric (invariant 5). Wall rows now emit 4 measurements each
(length, footprint, opening count, net), with the R4 viewer's
centerline/thickness restored on the length row.

New fixtures (hand-designed for the doctrines): `room_plan.dxf`,
`two_room_plan.dxf`, `wall_with_doorway.dxf` (D900 block corroborates the
gap), `opening_blocks.dxf`, `multi_storey_hint.dxf` (the phantom-opening
adversary). 21 new tests in `tests/unit/test_full_engine.py`.

### DXF labels + block-name markers (Worker B)

`ingestion/dxf/__init__.py` — TEXT/MTEXT captured into
`ParseResult.text_tokens` (modelspace order; empty/non-finite/z≠0 refused
to warnings, never measured); `is_opening_block_name` (exact pattern OR
pattern + digit-run-to-end — prefix matching would misclassify WALLSEG as
a W-block); `block_names_by_insert_handle` (INSERT handle → block name,
consumed by the engine's named-block detection). 20 new parser tests
(86 total in the file).

### PDF ingestion (Worker A)

`ingestion/pdf/__init__.py` — `parse_pdf` (pdfplumber; PyMuPDF remains
banned), same `ParseResult` contract as DXF: sheets, geometries,
text_tokens, honest warnings; `propose_scale_from_text` (regex `1:N`,
factor = N × 25.4/72 mm per drawing unit, PROPOSED-only, never
auto-applied); `PdfParseError` for structural refusals (corrupt, no
/Root, encrypted-and-locked). `takeoff/pdf_candidates.py` —
closed-area/length candidate detectors + `count_by_example`, deliberately
NOT registered as rules (PDF candidates propose, humans confirm —
consistent with the roadmap's "candidate detectors" wording and the
scale doctrine). Hand-authored ASCII PDF fixtures (byte-stable xref
tables computed programmatically), 51 new tests.

### Backend wiring + BOQ determinism (Lead)

`run_service` format dispatch (DXF + PDF; block_names passed for DXF),
`parse_service` PDF branch (units always unknown → NULL proposal → the
human gate governs; raster refused loudly with the format named),
`boq_service` (rule_id, unit) grouping + symmetric collision settle +
unmapped blockers persisted (the asyncpg insertmanyvalues sentinel trap
avoided by per-row flushes). Upload validation now runs the PDF parse
honestly: a corrupt PDF fails with `PdfParseError` in parse_warnings
(the old "only DXF parsing exists in V1" refusal is retired — PDF
parsing exists now).

## Latent defects caught this round (all closed with tests)

- Phantom openings between separate buildings (invariant 1) — caught by
  testing on `multi_storey_hint` BEFORE wiring the backend.
- Count-of-faces false zero — the count rule counting wall faces instead
  of zero slot geometries returned 2 for a zero-opening wall.
- `wall.net.area` replay dishonesty — the rule computed gross while the
  value claimed net; fixed to geometric subtraction from rule inputs.
- Face-gap slot None — clipping the slot to the host wall's extent made
  gap-span slots vanish (hi ≤ lo → zip strict crash); the geometric
  subtraction makes clipping unnecessary.
- Swapped width/height for vertical walls in block hosting; degenerate
  per-member bboxes; full-span covers check blind to straddling blocks.
- asyncpg insertmanyvalues sentinel mismatch on batched unmapped-exception
  inserts (str id params vs UUID returns) — 500s on the whole journey.
- Malformed run_id in `GET /runs/{id}/exceptions` 500'd (asyncpg UUID
  cast) instead of 404ing — fixed with a pre-check UUID guard.
- The E2E seeded only an m-item: with the collision doctrine the m2
  groups can never both auto-map — the E2E now resolves the unmapped
  blockers through the audited endpoint (the honest product flow).

## Verification actually executed

| Check | Result |
|---|---|
| `.venv/bin/pytest tests/unit tests/integration core/tests backend/tests` | **435 passed, 0 failed** (live PostgreSQL) |
| Browser E2E (api :8099 + worker + vite + Postgres) | **1 passed** (8.8s) — includes the unmapped-blocker resolution flow |
| Strict mypy (CI package list: core ingestion takeoff boq exports backend) | **83 files clean** |
| ruff check . | clean |
| lint-imports | **9 kept, 0 broken** |
| Reference-leak guard | clean (180 tracked files) |
| Banned-package check | clean (pdfplumber only; no PyMuPDF) |
| Frontend `npm run test` | **46 passed** |
| Frontend `npm run build` | tsc strict + Vite clean |
| Fixture regeneration | byte-stable (committed files unchanged; 5 new fixtures only) |
| ENGINE_VERSION | bumped 0.3.1 → **0.4.0** (new rules change the measured vocabulary; determinism pins re-verified byte-identical) |

## Parallel agents (orchestration per constraint)

Two worker agents (limit 3), non-overlapping ownership, Lead owned
integration:

- **Worker A — PDF slice**: parser + candidates + fixtures + 51 tests.
  Honest deviations, all reasonable: real embedded image in
  raster_only.pdf (true scanned-page simulation) with the whitespace
  variant separately test-pinned; entity_count includes images; an
  encrypted-but-owner-readable file is parsed (refusing readable content
  would be the dishonest option); metric-derived text positions asserted
  with approx (CI installs newest pdfplumber — geometry exact, metrics
  not); candidates module pins doctrine via usage patterns because its
  docstring quotes the doctrine.
- **Worker B — DXF text + block markers**: 20 tests. The block-name rule
  (exact or pattern+digit-run) was tightened at review — prefix matching
  would have misclassified WALLSEG.

## Known limitations (honest register)

- BOQ auto-mapping is unit-based per (rule_id, unit) group: a project
  whose catalogue has one m² item will always see the m² collision
  blockers (by design — who bills gross vs net is a human decision; the
  mapping UI is T084's "manual mapping UI later").
- PDF text-token positions are font-metric-derived (approx in tests);
  geometry is exact.
- PDF candidates are detectors, not rules: they run only when a human
  confirms (consistent with the candidate doctrine; rule registration is
  a post-V1 decision).
- Run history remains session-local in the UI (list endpoints still a
  later ticket).
- CI does not yet run the browser E2E job (service composition; the
  Makefile target documents the local invocation).
- The count-unit catalogue seeding in the E2E still only seeds the m
  item — the count group blockers are resolved in-test through the same
  audited endpoint (a count item could be seeded instead; the resolution
  path is the product path either way).

## Round 6 candidates (from the backlog, not committed to)

Raster ingestion + AI-vision candidates (T033/T048 — the deferred half of
this round), AI provider abstraction + guardrails (T060/T065), review
workspace depth (T114), BOQ editing endpoints (T086), xlsx/pdf export
(T101/T102), list-runs/list-exports endpoints, catalog management UI.
