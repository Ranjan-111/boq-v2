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
  `AI_LOW_CONFIDENCE`, `UNMAPPED_MEASUREMENT`, `MISSING_RATE`),
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
