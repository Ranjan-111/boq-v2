# Round 3 Report — Vertical Slice + Trust-Hardening Gate

**Verified:** 2026-09-10 · **HEAD:** `79a2181` · **Verdict:** Trust-hardening
gate verified (all nine defect areas addressed with regressions). Round 3
product scope (persistence, API wiring, viewer, browser E2E) remains open.

## Checkpoint sequence this round

1. **Takeover checkpoint (previous session):** baseline verification, INSERT
   placement/provenance fix, exact-quantity integration assertions, wall
   policy documented in docs/domain-model.md, adversarial tests drafted.
2. **Trust gate (this session, Lead + 2 worker agents):** all nine confirmed
   defect areas closed; missing coverage added; architecture contracts
   strengthened and guard-tested; foundation follow-up verified against live
   PostgreSQL.

## Trust gate verification — the nine defects, closed with evidence

### 1. Wall pairing cannot invent connectivity
`takeoff/wall_detection.py::are_offset_pair` now requires finite congruent
longitudinal support: both endpoints of the candidate partner must project
onto the full extent of the reference segment (within numeric tolerance
only), and lengths must be congruent. Infinite-line offset alone (the old
defect: `(0,0)→(1000,0)` pairing with `(10000,200)→(11000,200)`) no longer
produces a wall. `detect_walls` accepts only unique reciprocal pairs;
ambiguous candidates (three parallel faces) are refused together regardless
of input order. An explicit positive finite `max_wall_thickness` is required
— absent selection input leaves all edges unmatched for review (a recorded
exception), never a guessed construction thickness.
Tests: `tests/unit/test_trust_hardening.py` (no-longitudinal-support ×4,
ambiguity + order independence, explicit-thickness-required),
`tests/unit/test_wall_detection.py` (pair/overlap/unmatched matrix),
`test_disjoint_rule_replay_refused` (the rule itself re-validates).

### 2. Evidence is enforced at the measurement boundary
`takeoff/engine.py::measure_sheet` refuses the entire sheet when any
contributing geometry has empty or malformed source handles (`""`, `"0"`,
`"None"`, whitespace) with a BLOCKING missing_evidence exception — no
MEASURED record can exist without valid evidence. Evidence links carry the
source identity, version and full handle JSON.
Tests: `test_each_face_requires_evidence` (face 0 / face 1 / both),
`test_malformed_evidence_ref_refused` ×4, plus BOQ-side
`test_absent_evidence_refused` and `test_untrusted_measurement_refused`
(assembly independently refuses non-MEASURED or evidence-less records).

### 3. Replay identity is content-bound
`MeasurementInputs.constants` (schema `measurement-replay-v2`) binds:
canonical per-geometry snapshots (type, coordinates, handle chains, layer),
sheet, source identity/version (raw SHA-256 from the parser, or a
content-addressed snapshot for pure geometry callers), confirmed scale factor
and method, drawing/target units, rule id + version, engine version,
selection parameters (`max_wall_thickness`) and numeric tolerances
(`parallel_eps`, `offset_eps`). Changing geometry coordinates, source
identity, scale, units, or selection parameters changes the digest; input
ordering does not. `measurement_id` is uuid5(NAMESPACE_URL, digest) — a
durable identity for the immutable result, distinct per measurement context.
Tests: `test_digest_tracks_content_and_scale`, `test_digest_binds_selection_parameters`,
`test_digest_binds_source_identity_and_units` (also pins the uuid5 identity
derivation and anonymous content-addressed fallback).

### 4. Unsupported DXF geometry is refused, never reinterpreted
`ingestion/dxf/__init__.py::_unsupported_reason` refuses (with per-handle
warnings): CIRCLE/ARC (not measurable types), bulged LWPOLYLINE/POLYLINE,
non-default OCS/extrusion, nonzero elevation/Z/thickness, widths, 3D/mesh
polylines, fewer than two vertices, non-finite coordinates. `_explode_insert`
atomically refuses nested INSERT, MINSERT arrays, XClip paths (including
disabled clipping), nonzero insert elevation, non-finite transforms,
unsupported members, and partial/skipped virtual-entity transforms — an
INSERT contributes all of its geometry or none. Layer-0 members inherit the
INSERT's layer; explicit layers are preserved.
**Audit-found fix (Worker 1):** degenerate closed rings are now refused at
parse time — a closed polyline with fewer than 3 distinct vertices can never
become geometry. Previously a closed 1-vertex polyline passed the preflight
because the closure point was counted before the vertex-count check (becoming
a silent zero-length polyline), and a closed 2-vertex polyline became a
degenerate POLYGON whose `length_of` would double-count the span (20.0 for a
10-unit ring) if any future length rule consumed polygons. The preflight now
counts raw (pre-closure) vertices and requires 3 distinct vertices for closed
rings. Also verified by the audit: SPLINE/ELLIPSE/POINT/TEXT/MTEXT/HATCH/
SOLID (and more) are never silently dropped — all produce per-handle
warnings; paperspace can never leak into modelspace geometries; hand-edited
handleless entities parse with deterministic handles and a warning rather
than crashing; zero-length LINEs are captured as honest evidence and refused
downstream by the detector.
Tests: `tests/unit/test_dxf_parser.py` — `test_unsupported_semantics_refused_with_source_warning`
(16 kinds), `test_insert_refuses_whole_placement_instead_of_partial_geometry`
(9 kinds), `test_degenerate_closed_polylines_refused_not_reinterpreted` ×4,
`test_other_entity_types_never_silently_dropped` ×9,
`test_paperspace_content_never_leaks_into_modelspace_geometries`,
`test_warns_instead_of_crashing_on_hand_edited_handleless_entity`,
`test_insert_transform_failure_never_leaks_partial_geometry`,
`test_insert_layer_zero_inherits_placement_nonzero_layer_preserved`,
`test_inserted_member_coordinates_account_for_block_base_point`.
The radius-10 circle can no longer become a 2-point polyline: it produces a
warning and zero geometry.

### 5. Parser warnings reach the measurement boundary
`measure_parsed` (the file-caller entrypoint) folds the full parse result —
warnings, sheet presence/modelspace, raw source version — into the run:
any parse warning becomes BLOCKING parse_incomplete exceptions with zero
measurements; a missing/non-modelspace sheet or missing source SHA also
blocks. A sheet containing unsupported geometry can never look like a
complete authoritative takeoff. The library integration test now flows
through `measure_parsed` instead of the pure entrypoint.
Tests: `test_measure_parsed_blocks_on_parser_warnings`,
`test_measure_parsed_blocks_absent_sheet_and_missing_source_version`,
`test_measure_parsed_clean_run_measures_with_source_bound_replay`,
`tests/integration/test_vertical_slice.py` (uses `measure_parsed`).

### 6. Scale/sheet validation hardened
`ScaleCalibration.require_confirmed` refuses missing/non-finite/non-positive
factors; `measure_sheet` additionally refuses calibrations belonging to
another sheet, unknown drawing units (post-confirmation KeyError is now a
BLOCKING exception), invalid length/area target units, and geometry whose
handles belong to a different sheet (ambiguous_sheet). Human confirmation
status remains the first gate (PROPOSED/UNKNOWN still block everything).
Tests: `test_invalid_confirmed_scale_refused` (None/0/-1/NaN/Infinity),
`test_calibration_for_another_sheet_refused`, `test_unknown_drawing_units_refused_after_confirmation`,
`test_geometry_from_another_sheet_refused`, `tests/unit/test_engine.py` scale-gate suite.

### 7. BOQ/export trust boundaries (Round 3 level)
`boq/assembly.py::assemble_item` accepts only MEASURED/MEASURED_ZERO
records with (state↔value consistency), non-empty well-formed evidence,
canonical nonzero UUID identity (via the uuid5 measurement_id), no duplicate
references, one reconciled unit matching the catalogue item, non-negative
integer minor-unit rates/markups, supported two-decimal currencies.
`exports/csv_export.py` is an ungated-free zone: CSV bytes require an
`ExportApproval` context whose rows_digest (binding every serialized field
plus row order and full measurement identity) matches the current rows,
whose status is APPROVED/EXPORTED, and whose unresolved exceptions contain no
BLOCKING/REVIEW records; the export re-validates every row (recompute
invariant, identity validity/uniqueness, currency/quantity/price sanity)
independently of the approval. Per the domain contract, loading trusted
approval/exception scope, transactional stale detection, artifact storage
and audit are deferred to the API phase (Round 4) — pure validation is what
Round 3 requires.
**Audit-found fix (Worker 2):** the unresolved-exception gate originally
matched an allowlist (`severity in (BLOCKING, REVIEW)`) and therefore FAILED
OPEN on malformed severities (`"BLOCKING"` wrong-case, `None`, non-member
values all permitted export). It now fails closed: only genuine
`ExceptionSeverity.INFO` members pass; anything crossing the approval
boundary with a malformed severity blocks the export.
Tests: `tests/unit/test_assembly.py` (TestTrustGateRegressions,
TestExportApproval ×13 — unapproved states, unresolved exceptions, stale
snapshots, invalid rows despite matching approval, duplicates, mixed
currencies, byte-identical re-export; TestApprovalSnapshotBinding ×12 —
every serialized field, row order and identity order each invalidate the
approval; INFO severity is the only passable exception), plus
`test_export_gate_refuses_malformed_exception_severity` ×6 in the trust
suite (fail-closed regression), and the vertical-slice approval-gate journey.

### 8. Architecture enforcement actually bites
- The engine measures through the registered rule callable (`run_rule`);
  `test_engine_measures_through_registered_rule_callable` pins the registry
  discipline (unknown rule ids refuse; duplicate registration is a
  programming error).
- Domain services are structurally independent: `boq` and `exports` consume
  takeoff via Protocols, and two new import-linter contracts
  ("BOQ never reaches takeoff", "Exports never reach engines") make the
  boundary build-enforced. The old "Core imports nothing beyond stdlib"
  independence contract only checked core's subpackages against each other;
  it is replaced by a forbidden contract naming the third-party packages
  (requires `include_external_packages = true`), which empirically catches
  `core → sqlalchemy`.
- `tests/unit/test_architecture_guards.py` proves the contracts bite: each
  test injects a real forbidden import into an isolated tree copy (never the
  user's repo) and asserts import-linter fails with the right contract named.
  Contracts: **9 kept, 0 broken**.
- `docs/architecture.md` §E now documents the enforced graph, including the
  stricter-than-before service independence (the previously documented
  `exports → boq, pricing` allowance never matched the enforced layers
  contract; the enforced graph — services import core only — is now what the
  doc says).

### 9. Foundation follow-up (verified against live PostgreSQL)
Docker was started this session; the previously-always-skipped DB tests ran
for the first time against real Postgres 16:
- **Migrations:** all 3 Alembic migration tests pass on a real scratch
  database (21 tables, up/down round-trip).
- **Authorization:** project queries scope to creator and soft-deleted
  projects 404 — verified against the real query path
  (`backend/tests/test_project_authorization.py`, now actually running).
  Fixing its run surfaced a latent test-only defect: batched ORM inserts of
  2+ string-UUID PK rows fail asyncpg insertmanyvalues sentinel matching
  (single-row inserts — the application's real request pattern — are fine;
  the model keeps `Mapped[str]` + native `Uuid`). The test now follows the
  real insertion pattern and the limitation is documented here.
- **Lifespan:** `create_app` owns engine/sessionmaker lifetime; init, error
  and dispose paths covered (`backend/tests/test_lifespan.py`).
- **Makefile:** `test`/`test-integration`/`test-all` cover
  `core/tests backend/tests tests`; `lint` propagates failures
  (`backend/tests/test_makefile.py`).

## Verification actually executed (this checkpoint)

| Check | Result |
|---|---|
| `.venv/bin/pytest core/tests backend/tests tests` | **300 passed, 0 skipped** (live PostgreSQL; Docker `boqv2-postgres-1`) |
| Trust-hardening suite (`tests/unit/test_trust_hardening.py`) | 34 passed |
| Architecture guards (`tests/unit/test_architecture_guards.py`) | 8 passed (incl. 3 violation-injection proofs + core third-party proof) |
| DXF parser suite | 67 passed (16 unsupported-semantics kinds, 9 INSERT refusal kinds, degenerate-ring refusal, paperspace/handle-hardening cases) |
| Strict mypy (CI package list) | **66 files clean** |
| ruff check . | clean |
| lint-imports | **9 kept, 0 broken** (was 6; contracts strengthened, not just added) |
| Reference-leak guard | clean, 142 tracked files (R3 sources now tracked) |
| git diff --check | clean |
| Frontend `npm run test` | 5 passed |
| Frontend `npm run build` | TypeScript + Vite passed |

One JWT InsecureKeyLengthWarning (23-byte test key) remains in output; it is
a test-fixture warning, not a product defect.

## Parallel agents (orchestration per constraint)

Two worker agents ran (limit 3), both completing with verified reports:

- **Worker 1 (DXF refusal coverage):** ran the parser suite (37 → 67 tests
  after its additions), ruff, mypy and the full suite. Added 30 test cases
  covering entity types never silently dropped (SPLINE/ELLIPSE/POINT/TEXT/
  MTEXT/HATCH/SOLID ×9), legacy-POLYLINE refusal branches ×5, non-finite
  coordinates ×2, INSERT refusal paths ×4 (missing/empty block, degenerate
  member, non-finite transform), paperspace-leak prevention, hand-edited
  handleless entities, zero-length LINEs, and block base-point alignment.
  Found and reported the closed-ring preflight defect (fixed by the Lead,
  see §4).
- **Worker 2 (BOQ/export + foundation):** ran the assembly suite (43 → 55
  tests after its additions), backend suites (52 passed with live Postgres,
  repeated), ruff, and empirical digest/severity probes. Added
  TestApprovalSnapshotBinding (12 cases: every serialized field, row order,
  identity-within-row order each invalidate an approval; INFO severity is
  the only passable exception). Found and reported the fail-open severity
  gate (fixed by the Lead, see §7); confirmed creator scoping, lifespan
  registration, Makefile gates and measurement_id durability with
  file:line evidence.

The Lead owned wall pairing, evidence, replay, scale/sheet, architecture
contracts, the integration test, both audit-found source fixes and all
reports. No worker edited another agent's owned files; no source files were
touched by workers (both defects were reported for lead action, per scope).

## PostgreSQL status

Live PostgreSQL 16 (docker, `boq:boq@5432`) was available for this
checkpoint: migration and authorization tests ran against it (the job queue
has no dedicated test file yet — its SKIP-LOCKED/concurrency behavior
remains untested against a live DB and stays on the Round 4 list). No live
API server run, no MinIO/storage integration, no performance suite.

## Browser E2E status

Not implemented; explicitly out of the Round 3 trust-gate scope per the
round instructions. No browser workflow claim is made.

## Files changed (Round 3 cumulative — committed in 11 ticket-sized commits, pushed as 79a2181..902c161)

- Parser/geometry: `ingestion/dxf/__init__.py`, `core/geometry/__init__.py`,
  `core/units/geometry_units.py`, `pyproject.toml` (types-shapely dep,
  import-linter 9 contracts, include_external_packages)
- Takeoff: `takeoff/kernel.py`, `takeoff/wall_detection.py`,
  `takeoff/engine.py`, `takeoff/rules/__init__.py`
- BOQ/export: `boq/assembly.py`, `exports/csv_export.py`
- Backend foundation: `backend/app/main.py`, `backend/app/api/auth.py`,
  `backend/app/api/projects.py`, `backend/app/db/dependencies.py` (new),
  `backend/tests/conftest.py` (new), `backend/tests/test_lifespan.py` (new),
  `backend/tests/test_makefile.py` (new),
  `backend/tests/test_project_authorization.py` (new),
  `backend/tests/test_router_imports.py` (new), `backend/tests/test_migrations.py`,
  `Makefile`
- Tests: `tests/unit/test_trust_hardening.py` (new, 34 tests),
  `tests/unit/test_architecture_guards.py` (new, 8 tests),
  `tests/unit/test_dxf_parser.py`, `test_kernel.py`, `test_wall_detection.py`,
  `test_engine.py`, `test_assembly.py`, `test_reference_leak_guard.py`,
  `tests/integration/test_vertical_slice.py`, `tests/fixtures/` (7 real DXF
  fixtures + generator)
- Docs: `docs/domain-model.md` (trust semantics §), `docs/architecture.md`
  (§E enforcement notes), `docs/ticket-backlog.md` (R3 markers),
  this report, `latest-status.md`, `project-progress.md`,
  `tool-availability.md`

## Known limitations (honest register)

1. Round 3 product scope remains: no persisted runs/measurements/BOQ, no
   API wiring beyond auth/projects, no upload/viewer UI, no browser E2E.
2. ExportApproval is a pure record; the orchestrator that loads a trusted
   persisted approval + complete unresolved-exception scope is Round 4
   (stated in code and domain-model.md).
3. Batched ORM inserts of 2+ string-UUID PKs fail asyncpg insertmanyvalues
   sentinel matching (single-row inserts fine; documented above).
4. import-linter's `include_external_packages = true` now scans external
   imports; if a future allowed dependency is added to core, the forbidden
   list must be updated deliberately (that is the point).
5. The reference-leak guard scans tracked files only (142); untracked files
   are outside its scope — commit nothing without re-running it (the
   context.md near-miss proves it).
6. Round 3 is pushed (HEAD 902c161) and **remote CI run 34422918941 is
   green — all five jobs (lint, architecture, tests, license-scan,
   frontend)**. The clean-worktree reproduction run BEFORE pushing caught two
   would-be-CI failures: context.md containing the banned OCErp/CWICR names
   (reference guard scans tracked files only; untracked, so local runs
   passed) and a venv-path assumption in the architecture guard test.

## NEXT ROUND HANDOFF (Round 4)

**Objective:** make the verified trust boundary a product: persist and serve
the pipeline through the approved API contracts.

1. Round 3 is committed, pushed and CI-green (11 commits 281973c..902c161;
   remote run 34422918941: lint/architecture/tests/license-scan/frontend all
   success). Branch forward from 902c161.
2. Upload/drawing/sheet routes → storage port + magic-byte validation
   (T019 router wiring), parse jobs via the SKIP-LOCKED queue, worker
   handlers beyond ping.
3. Persist sheets/scale confirmations (human gate API), runs + measurement
   records + evidence (the `MeasurementRecord` shape is the persistence
   contract), exception queue endpoints.
4. BOQ persistence: mappings, draft rows from measurements, approval
   states with transactional stale detection; export endpoints enforcing the
   `ExportApproval` contract server-side (load trusted scope — never client
   input), immutable artifacts + provenance sidecar (T103).
5. Frontend: upload/progress, sheet/scale confirmation, viewer with
   evidence highlight (T070), exceptions queue, BOQ workspace basics.
6. Browser E2E of the gated DXF→CSV journey (the Round 3 exit-criterion
   demo deferred into Round 4 by explicit instruction).
7. Keep the trust invariants green: every new surface must refuse rather
   than guess (the Round 3 test suites are the regression contract).
