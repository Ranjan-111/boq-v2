# Domain Model — v1.0 (Foundational Contract)

**Status:** Round 1 · Authored by Lead Architect **before** any dependent build work.
**Rule:** No agent may invent a competing version of these entities/states. Additions require a Lead-approved doc change.

## Design stance

- Provenance is a first-class citizen, woven through every entity, not a bolt-on.
- AI outputs are **physically separated** from measured/deterministic data.
- States are explicit, small, and testable — every transition is auditable.
- IDs: UUIDs (v7 preferred for sortability). All monetary/quantity values stored
  as integers with explicit scale (e.g., minor units) or `NUMERIC(18,6)` — never floats.

## Entity map

```
Project 1───* Storey 1───* DrawingSheet *───1 DrawingFile
                                        │
Project 1───* MeasurementRun ───────────┤ (run consumes sheets)
                     │                  │
                     *──1 ScaleCalibration (per sheet, human-confirmed)
                     │
                     * Element *───1 Geometry (normalized, with source handles)
                     │     │
                     │     *──* EvidenceLink (→ raw entities in source file)
                     │
                     * Measurement (deterministic quantity, has rule + provenance)
                     │     │
                     │     *──* Exception (uncertainty/blocking)
                     │
Project 1───* CatalogueItem (region-scoped)
             Rate 1──* CatalogueItem  (vendor/scoped rates)
             
Measurement *───1 CatalogueItem (mapping → produces BoqItem)
             — Round 5 mapping doctrine: group by (rule_id, unit), never bare
             unit (room m² ≠ wall m²); one catalogue item maps at most one
             group — collisions block ALL claimants symmetrically (gross and
             net never bill as one line); unmapped groups are BLOCKING
             `UNMAPPED_MEASUREMENT` exceptions, resolved only by a human.
BoqItem 1──* PriceComponent?  → NO: BoqItem carries rate + markup + computed total
Boq 1───* BoqSection 1───* BoqItem
Boq 1───1 Approval
Boq 1───* ExportArtifact
ReviewDecision *──1 (Measurement | Exception | Mapping)  — audit trail rows
```

## Entities

### Project
- `id`, `name`, `client_name?`, `region_code` (e.g., `IN`), `currency` (ISO 4217), `created_at`
- Owns everything. Soft-delete later; not V1.

### Storey
- `id`, `project_id`, `name` (e.g., "GF", "L1"), `elevation_mm?`, `sort_order`

### DrawingFile
- `id`, `project_id`, `filename`, `mime/format` (`pdf|dxf|raster`), `size_bytes`,
  `storage_key`, `sha256`, `uploaded_by`, `uploaded_at`
- Immutable once stored; a new file version = new DrawingFile (simpler than version graphs).

### DrawingSheet
- `id`, `drawing_file_id`, `page_number`/`layout_index`, `title?`, `sheet_type?`
  (proposed by AI: `plan|section|elevation|detail|schedule|unknown` + confidence)
- One PDF page, one DXF layout, or one image = one sheet.

### ScaleCalibration
- `id`, `sheet_id`, `status`: `PROPOSED | CONFIRMED | UNKNOWN`
- `method`: `detected_from_dxf_units | bar_scale_detected | user_two_point | user_known_ratio`
- `units_per_drawing_unit` (e.g., mm per drawing unit), `confidence`, `confirmed_by?`, `confirmed_at?`
- **Rule: NO measurement may be computed on a sheet whose calibration is not
  CONFIRMED.** Auto-detection only ever produces PROPOSED. This is the "never
  silently guess scale" principle, enforced in the engine, not in UI hints.

### MeasurementRun
- `id`, `project_id`, `input` (set of DrawingFile ids + params), `status`:
  `QUEUED → RUNNING → COMPLETED | FAILED | COMPLETED_WITH_EXCEPTIONS`
- `started_at`, `finished_at`, `engine_version`, `stats_json` (counts per state)
- Immutable snapshot semantics: re-running creates a new run; old runs stay for audit.

### Element
- `id`, `run_id`, `sheet_id`, `element_type` (wall, room, door, window, slab, …),
  `element_type_source`: `geometry_deterministic | ai_classified | human_set`
  (+ if AI: `ai_confidence`, `ai_model`, `ai_explanation`)
- `geometry` → normalized geometry record
- `label?` (e.g., room name from drawing text, with `label_evidence`)

### Geometry (normalized, versioned schema)
- `id`, `element_id`, `geom_type`: `point | line | polyline | polygon | multi_polygon`
- `coordinates` (JSON, in **drawing units**), `source_format` (`pdf_vector|dxf_entity|raster_region`),
- `source_handles`: array of `{format, sheet_ref, handle_or_path}` — the provenance
  anchors back to raw entities. For raster: bounding regions/crop refs.
- `derived_from?`: if geometry was normalized from another (e.g., polygon built
  from wall centerlines), links to source geometry ids. AI never appears here.

### Measurement
- `id`, `element_id`, `run_id`, `quantity_type` (`length|area|count|volume_later`),
- `value` (exact, in project units after scale + unit conversion), `unit`
  (`mm|m|m2|count`…), `rule_id` (which deterministic rule computed it),
- `inputs`: array of `{geometry_id | element_id | constant}` — full input provenance,
- `status`: see measurement states below,
- `computation` replayable: `{rule_id, engine_version, inputs_digest}` — same inputs +
  rule = same value, verified in tests.

### Exception
- `id`, `run_id`, `measurement_id?`, `element_id?`, `sheet_id?`
- `code` (catalogued, e.g., `SCALE_UNCONFIRMED`, `OPEN_POLYLINE`, `OVERLAP_DETECTED`,
  `AI_LOW_CONFIDENCE`, `UNMAPPED_MEASUREMENT`, `MISSING_RATE`,
  `ROOM_NOT_ENCLOSED`, `OPENING_AMBIGUOUS` — a bare collinear wall gap with
  no corroborating door/window block: never a counted opening),
- `severity`: `BLOCKING | REVIEW | INFO`
- `message`, `evidence` (auto-gathered refs), `ai_explanation?`, `created_at`
- Resolved by ReviewDecision or by fixing inputs (run re-executes → exception re-evaluated).

### EvidenceLink
- `id`, `element_id | measurement_id | exception_id`, `kind`:
  `geometry | text_token | image_region | dimension_annotation | scale_bar`
- `ref` (geometry_id / token range / crop bbox), `note?`
- Powers the drawing-viewer highlight overlays. Every Measurement MUST have ≥1
  evidence link or be BLOCKING-invalid.

### CatalogueItem
- `id`, `region_code`, `code`, `description`, `unit` (must reconcile with measurement units),
  `category_path` (e.g., `concrete/in-situ/footing`), `default_rate_minor?` or `rate_ref`,
- `source` (`seed_data | manual | imported`), `source_attribution` (license note for imported data!)

### Rate
- `id`, `catalogue_item_id`, `scope`: `DEFAULT | PROJECT | VENDOR`
- `vendor?`, `currency`, `amount_minor` (integer minor units), `valid_from?`,
- Provenance: `entered_by?`, `imported_from?`. Price provenance = which rate id + which
  measurement value produced a total.

### Boq / BoqSection / BoqItem
- Boq: `id`, `project_id`, `version`, `status`: see approval states.
- BoqSection: `id`, `boq_id`, `parent_section_id?`, `code`, `title`, `sort_order`
- BoqItem: `id`, `section_id`, `catalogue_item_id?`, `measurement_id?`
  (a BoqItem either maps ≥1 measurements through its catalogue item, or is a
  manual/PC-sum line with `origin: MANUAL` and audit trail),
  `description` (AI-drafted allowed, human-editable, diff-tracked),
  `quantity` (from mapped measurements, recomputed on change), `unit`,
  `rate_minor`, `rate_scope`, `markup_pct_bp` (basis points), `total_minor` (computed),
  `rounding_rule`, `provenance` ({measurement_ids, rate_id, rule}).

### Approval
- `id`, `boq_id`, `state`: see approval states, `actor`, `at`, `note?`
- Stale detection: any measurement/rate/mapping change after APPROVED flips Boq
  to `STALE_APPROVED` (exportable only after re-approval). 

### ExportArtifact
- `id`, `boq_id`, `format` (`csv|xlsx|pdf|json_sidecar`), `storage_key`, `sha256`,
  `manifest` (boq_version, engine_version, run_ids, row_count, totals), `created_at`
- Reproducibility: manifest contains everything to re-generate deterministically.

### User / Account
- `id`, `email`, `role`: `OWNER | ESTIMATOR | VIEWER` (V1: single workspace, few users, no teams)

## State machines

### Measurement states

```
MEASURED          — deterministic value, ≥1 evidence, no exceptions
MEASURED_ZERO     — computed exactly 0 (e.g., opening deduction to zero) — INFO-flagged, not silent
NEEDS_REVIEW      — has non-blocking exceptions (low AI confidence, ambiguity)
NOT_MEASURABLE    — geometry present but rule cannot compute (open polyline for area, …)
BLOCKED           — blocking exception (scale unconfirmed, missing evidence, …)
```

Transitions trigger on: run completion (initial), exception raise/clear,
review decision. All transitions append audit rows. **AI confidence never
changes a measurement's value — only its review state.**

### Approval states

```
DRAFT → IN_REVIEW → REVIEWED → APPROVED → EXPORTED
                ↘ (rejected) → DRAFT
APPROVED --(inputs changed)--> STALE_APPROVED --(re-approve)--> APPROVED
```

- Export requires `APPROVED` (or `EXPORTED` re-export of same version).
- Export writes an immutable artifact + manifest; approval is per Boq version.

### Run states

```
QUEUED → RUNNING → COMPLETED
                 → COMPLETED_WITH_EXCEPTIONS   (normal — review queue populated)
                 → FAILED                       (infra/engine error, retriable)
```

## Audit trail (ReviewDecision)

Append-only table: `id`, `at`, `actor`, `action`
(`CONFIRM_SCALE | OVERRIDE_ELEMENT_TYPE | ACCEPT_MEASUREMENT | CORRECT_QUANTITY |
 RESOLVE_EXCEPTION | MAP_CATALOGUE | SET_RATE | APPROVE | REJECT | EXPORT …`),
`subject_type`, `subject_id`, `before` (JSON), `after` (JSON), `reason?`.

Human quantity corrections never mutate the original Measurement row; they
create `Measurement.correction` linkage + audit entry, and BOQ recomputes from
corrected values with provenance `human_correction` — original always visible.

## Invariants (enforced by engine + tests)

1. A Measurement with no EvidenceLink cannot be MEASURED — must be BLOCKED.
2. No measurement computes on a sheet with scale ≠ CONFIRMED.
3. AI fields may only be written by the AI layer into `*_suggested` columns /
   separate tables — deterministic fields are type-system-guarded.
4. Export requires: zero BLOCKING exceptions, all measurements MEASURED or
   human-accepted, all BoqItems priced or explicitly exempt (PC sums).
5. Every BoqItem total is recomputable from (quantity, rate, markup) — no cached
   totals without a recompute check in tests.
6. Every run's outputs reference `engine_version` — old runs replayable.

## Round 3 trust-hardening semantics (2026-09-10)

The straight-wall slice supports finite coplanar 2D straight faces only. A pair
must have matching longitudinal endpoints (either orientation), congruent
lengths and constant positive perpendicular separation within explicit numeric
tolerances. Partial overlaps and disjoint extents are refused; no extrapolation
or invented connectors. The caller supplies a positive finite maximum wall
thickness in drawing units, as a recorded selection parameter; absent policy
produces review/refusal, not a guessed construction thickness. A candidate
edge must have exactly one eligible partner and that partner must reciprocate.
Ambiguous connected candidates are refused together, independent of ordering.
Unmatched wall-layer edges are reported for review. A layer hint is a candidate
selector, not a universal guarantee that arbitrary CAD geometry is a wall.

Every contributing face needs nonempty valid source handles in the requested
sheet. Missing evidence blocks measurement. Invalid scale (missing/nonfinite/
nonpositive), mismatched sheet, unknown units or unsupported parse warnings
block the sheet without producing authoritative quantities. Parser warnings
are propagated by the pure parse-result measurement entry point; low-level
geometry callers must supply their full source context and warnings.

### Engine 0.8.0 — overlap-window wall pairing (2026-09-14)

Round 3 refused partial overlaps between wall faces: a whole-face congruence
requirement. On real architectural drawings (junctions, openings splitting
one face into fragments) that rule refuses the majority of drawn walls —
measured on the reference corpus at 26 walls from 354 wall faces. Engine
0.8.0 generalizes pairing from whole faces to **drawn windows**:

* A parallel, constant-positive-separation face pair is a wall over the
  **intersection of the two faces' drawn longitudinal spans** — and only
  over that span. No wall is measured over geometry either face does not
  draw (the no-extrapolation invariant, applied per-window rather than
  per-face).
* One face may pair with several partners over **disjoint** windows: the
  split-face doorway (one continuous face beside two fragments) yields two
  walls, each with full two-face support. This is the junction-splitting
  behavior that makes real drawings measurable.
* Windows that **overlap on a shared face** are a true ambiguity: every
  claimant of the conflict group is refused together, independent of order
  (the Round 3 three-parallel-faces doctrine generalized from faces to
  windows).
* Disjoint face extents (no drawn overlap) remain refused.
* Each accepted window's truncated face pair must independently satisfy the
  same congruent-support gate the replay rule applies — detection can never
  accept what `wall.centerline.length.v1` would refuse, so every measurement
  stays replayable from its two-face inputs alone.
* Face spans consumed by no window surface as unmatched-edge fragments for
  review — a drawing's leftover geometry is never silently absorbed.

The reciprocal-unique-pairing, explicit-max-thickness, and tolerance-binding
rules are unchanged.

### Engine 0.9.0 — centerline junction completion (2026-09-14)

Engine 0.8.0 windows truncate every wall to the span both faces draw, so on
real drawings wall centerlines stop at corners: each centerline ends at the
partner wall's face (half a thickness short of the partner's centerline), and
exact-endpoint noding finds no closed ring — measured on the reference
corpus at 76 walls, 0 rooms. Engine 0.9.0 completes legitimate junctions in
the ROOM GRAPH ONLY, never in wall measurements:

* **Perpendicular completion (corner / T-junction).** For an endpoint P of
  wall A with outward direction d, a non-parallel wall B whose centerline
  intersects A's at I, forward from P, is a junction partner when BOTH
  sides stay within the drawn thickness: A's extension |P→I| must not exceed
  t_B/2 (A's centerline may reach at most B's centerline — the material B
  actually draws), and B's side must either need no extension at all (I
  lies within B's drawn span: a T-junction) or extend B's own near endpoint
  by at most t_A/2 (a mutual corner, each centerline stopping at the other's
  face). The completed junction point is the centerline intersection I.
* **Uniqueness is required per endpoint.** All partners that satisfy the
  bounds must resolve to the SAME junction point I; a single endpoint with
  two distinct feasible junction points is `junction_ambiguous` (REVIEW) and
  the endpoint stays open — the engine never picks the nearer wall.
* **Collinear doorway bridge.** Two walls on one centerline line (same
  thickness within tolerance) with a gap between their finite spans are
  bridged ONLY when drawn evidence occupies the gap, in two classes:
  * *Seam* — a drawn wall-layer face extends through the whole gap inside
    the wall band: the walls are two 0.8.0 window measurements of ONE drawn
    wall-run (the drawing supports continuity; the seam is a pairing
    artifact, not an opening). The corroborating line is the ORIGINAL drawn
    line from the sheet, never the walls' own window-truncated edge
    geometries — those are cut at the seam by construction.
  * *Doorway* — an opening/header-layer geometry (the opening detector's
    layer hints plus header layers) whose VERTEX EXTENT covers the full gap
    span and stays in the wall band's corridor: real drawings draw doorways
    as jamb lines, swing symbols, and closed 4-vertex header rectangles
    spanning exactly the gap; evidence of any vertex count corroborates via
    its extent, not only two-point segments.
  The bridge is the corridor segment on the shared line across the gap,
  reusing the walls' OWN endpoint coordinates. Three guards refuse the rest:
  the gap must be EMPTY (a third collinear wall inside the interval means
  the outer pair is not adjacent — bridging them would invent a corridor
  through a real wall); evidence must COVER the full span (a short line
  touching inside a long gap corroborates nothing — probe-measured on the
  reference drawing, where A-OPENING headers overlap every gap on a line
  and a coverage-less rule manufactured 88 bridges including phantom
  corridors); and a bare gap stays open (two separate structures is equally
  plausible; the multi-storey fixture demonstrates the refusal).
* **Bridging never invents wall.** Connectors and bridges are inputs to room
  polygonization only: no wall measurement, element, or evidence row ever
  derives from a connector; wall rows are byte-identical to 0.8.0. A room
  gross ring still runs along wall CENTERLINES (plus the zero-area junction
  points); net areas still subtract the wall footprints.
* **Junction points are canonical.** The same junction computed in two
  walls' frames differs in the last float bits; every connector end is
  canonicalized onto the merged link's single point (noding never snaps
  near-coincident endpoints — a last-bit split breaks every downstream ring).
* **Closure rectangles.** Each perpendicular connector contributes the
  owning wall's footprint band extended from its endpoint to I — the corner
  nub, drawn material the 0.8.0 windows refuse to measure (outer faces meet,
  inner faces stop). NET room area subtracts these beside the wall
  footprints; the extension is bounded by the partner's drawn
  half-thickness, the same bound that admitted the junction. GROSS is
  unchanged — the nub sits inside the centerline ring.
* **Provenance.** A completed junction carries the partner walls' source
  handles as derived geometry; a doorway bridge additionally carries the
  corroborating opening-layer line's handles (the drawn evidence that made
  the bridge legitimate).
* **Open gaps are honest.** Endpoints with no bounded unique partner
  produce nothing — no exception, no phantom room. `room_not_enclosed`
  still fires (REVIEW) when ≥3 walls exist and no face closes, and
  `junction_ambiguous` surfaces per ambiguous endpoint.

Replay identity binds canonical input geometry and handle chains, sheet,
source identity/version (raw SHA-256 for parsed files), confirmed scale,
drawing/target units, rule id/version, engine version, selection parameters
and numeric tolerances. Pure geometry callers use a content-addressed geometry
snapshot if no external source identity is supplied. A deterministic UUID
from that digest identifies the immutable measurement result, never a display
label. Database run/version ownership is attached later by orchestration.

BOQ assembly accepts only evidenced MEASURED/MEASURED_ZERO records with durable
identity and rejects duplicate measurement references. Human acceptance remains
unavailable until audited review exists. Public CSV export requires a trusted
approval context bound to the current complete row snapshot, exportable state
and no unresolved blocking/review exceptions. Pure validation is implemented
now; loading trusted approval/exception scope, transactional stale detection,
artifact storage and audit remain application responsibilities for the later
API phase. Re-export after any upstream mutation must become STALE_APPROVED.
