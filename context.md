# context.md — BOQ V2 Project Handoff Context

**Purpose:** Complete context for continuing this project in a new chat. Read this first.
**Written:** 2026-09-10 · **Repo:** `~/Documents/prj/boq v2` · **Remote:** https://github.com/Ranjan-111/boq-v2.git
**Status:** Round 1 ✅ · Round 2 ✅ (CI green) · **Round 3 IN PROGRESS (vertical slice implemented, trust-hardening gate unfinished)**

---

## 1. What this project is

**boq-v2** — AI-native construction takeoff & Bill of Quantities product. Workflow:
`DRAWING → UNDERSTAND → MEASURE → REVIEW → BOQ → PRICE → APPROVE → EXPORT`

It is explicitly **NOT an OCErp clone**. OpenConstructionERP (in `reference/`, local-only, gitignored) is a read-only behavioral/pattern reference.

**The core goal (quote):** "make it impossible for the system to silently produce a believable but wrong quantity."
Correctness > speed · Evidence > assumption · Determinism > AI guessing · Explicit refusal > fabricated measurement.

### Non-negotiable rules (all rounds)

- Deterministic calculations are authoritative. AI never writes quantities — AI outputs are proposals only.
- AI can never invent scale; scale confirmation is a human gate (`ScaleCalibration.require_confirmed()` raises).
- Every measured quantity requires provenance/evidence; missing evidence → BLOCKING, never a silent MEASURED.
- Ambiguous geometry → review/blocking state per the approved state machine, never a guess.
- Money = integer minor units, banker's rounding (ROUND_HALF_EVEN); markup in integer basis points.
- Only confirmed quantities enter final totals; blocking issues prevent approval/export.
- Never fake tests. Never mark a ticket DONE without verification.
- Alembic is the only schema authority (no `create_all` anywhere).

### Licensing (binding, from Round 1 license analysis)

- OCErp is **AGPL-3.0** — ZERO code or data copying (including their catalog CSVs; "CWICR" trademark banned).
- Pattern-inspire only, with the reference repo closed while writing. Never commit `reference/` to git.
- **PyMuPDF is banned** (AGPL cascade) → use pdfplumber/pypdf/pypdfium2.
- Dependency licenses: MIT/Apache/BSD/PSF/Unlicense only. python-jose was already swapped to PyJWT in Round 2 (jose drags an unfixed `ecdsa` advisory).

---

## 2. Stack & structure

- **Python 3.13 + uv**, single root `pyproject.toml`, hatchling, venv at `.venv/`.
- Packages: `core/ ingestion/ takeoff/ classification/ provenance/ review/ boq/ catalog/ pricing/ exports/ backend/` — each has `py.typed` and `__init__.py` (Round 2 lesson: an unanchored `storage/` gitignore rule once hid `backend/app/storage/` from git entirely; it's now `/storage/`).
- Backend: FastAPI + Pydantic v2 + SQLAlchemy 2 async + asyncpg + Alembic + structlog + PyJWT + bcrypt. 21 domain tables; SKIP-LOCKED Postgres job queue; storage port (local/memory adapters); magic-byte upload validation.
- Frontend: React 18 + Vite + TS strict + TanStack Query + zustand + Tailwind (`frontend/`). Auth/projects/workspace shell done; Round 3 UI work is placeholder-level.
- Infra: docker-compose (postgres:16-alpine `boq:boq@5432`, minio). Makefile targets: `venv install lint arch guards test db-up api worker`.
- Guards: import-linter (6 contracts, config in `pyproject.toml [tool.importlinter]`), `tools/guards/reference_leak.py`, CI workflow `.github/workflows/ci.yml` (5 jobs: lint, architecture, tests, license-scan, frontend).

### Key Round 3 source files (all currently UNCOMMITTED — see §5)

| File | Role |
|---|---|
| `ingestion/dxf/__init__.py` | ezdxf parser: normalized geometry, dxf.handle capture, modelspace-first, INSUNITS, INSERT placements via virtual_entities (fixed double-translation + member provenance) |
| `core/geometry/__init__.py` | `NormalizedGeometry`, `SourceHandleRef`, `SheetSummary`, `ParseResult` (pure data; coordinates in DRAWING units, never pre-scaled) |
| `takeoff/kernel.py` | Shapely geometry kernel (area/length, self-intersection refusal) |
| `takeoff/wall_detection.py` | Wall pairing from parallel line pairs — congruent finite support, reciprocal unique pairs, ambiguity refused, explicit `max_thickness` required |
| `takeoff/engine.py` | `measure_sheet` / `measure_parsed` (T049): scale gate, sheet binding, evidence enforcement, parse-warning blocking, content-bound replay digest, `MeasurementRecord`/`RunOutput` |
| `takeoff/rules/__init__.py` | Versioned rule registry (`run_rule`, ENGINE_VERSION) |
| `boq/assembly.py` | BOQ row assembly, unit reconciliation, rate×qty, markup (pure) |
| `exports/csv_export.py` | Deterministic CSV serializer |
| `tests/fixtures/` | Real generated DXF fixtures (incl. translated/rotated/mirrored INSERT) |
| `tests/integration/test_vertical_slice.py` | Library-level E2E: DXF → walls (exact 4 m & 6 m) → BOQ (10 m, 913750 minor) → CSV values |
| `tests/unit/test_trust_hardening.py` | NEW adversarial tests (see §6 — some were failing when the last session ended) |
| `tests/unit/test_dxf_parser.py, test_kernel.py, test_wall_detection.py, test_engine.py, test_assembly.py` | Round 3 unit tests |
| `backend/tests/test_lifespan.py, test_makefile.py, test_project_authorization.py, test_router_imports.py` + `backend/app/db/dependencies.py` | Foundation follow-up from the takeover session |
| `docs/reports/round-3-report.md` | Round 3 checkpoint report (contains the 9 confirmed open defects) |
| `docs/reports/tool-availability.md` | Tool testing log (update whenever a tool is tested/fails) |

---

## 3. Round history

### Round 1 ✅ — Discovery + architecture (committed)
11 docs in `docs/`: product-vision, non-goals, domain-model (foundational contract), api-contract, architecture (perf targets §G, AI provider boundary §J), agent-plan, reuse-matrix, license-analysis (binding §4), implementation-roadmap, ticket-backlog (T001–T129), testing-strategy. 5 parallel OCErp audits via subagents. 8 commits.

### Round 2 ✅ — Foundation (committed, pushed, CI GREEN)
T010–T021 done: monorepo scaffold, core domain (typed IDs, enums, state machines, provenance records, Money, ScaleCalibration/units), 21-table SQLAlchemy models + Alembic baseline, FastAPI app (JWT auth register/login/me, projects CRUD w/ cursor pagination, RFC7807 errors, request-id middleware, /healthz /readyz), SKIP-LOCKED job queue + worker, storage abstraction, magic-byte upload validation, CI + all guards, React frontend shell.

CI war story (important lessons): first remote run failed 3/5 jobs. Root causes fixed in commits `3771b77`, `ab35824`, `79a2181`:
1. `.gitignore` `storage/` (unanchored) matched `backend/app/storage/` — the source package was never committed; local green, CI red. **Always reproduce CI in a clean `git worktree` checkout before pushing.**
2. mypy strict had never run over the full tree (97 errors fixed; all files now pass `--strict`).
3. ci.yml bugs: alembic `-c` arg order; pytest scope only ran 3 of 90 tests.

**CI verified green:** remote run 34273889351 — all 5 jobs. Latest pushed HEAD: `79a2181`.

### Round 3 — IN PROGRESS (all work uncommitted in the working tree)
**Objective:** vertical slice — DXF → parse → stable source handles → normalized geometry → wall detection → deterministic wall quantity → evidence/provenance → BOQ row → CSV export → (browser E2E later).

Done so far (verified: 151 passed, 3 skipped):
- DXF parser with handles, modelspace, INSUNITS, INSERT fix (virtual_entities applies placement once; member identity resolves through original source entity)
- Geometry kernel, wall detection, rule registry, measurement engine with exceptions
- BOQ assembly + deterministic CSV; library-level vertical-slice integration test with exact-quantity assertions
- Trust-hardening pass started: wall policy documented in `docs/domain-model.md`; adversarial tests written in `tests/unit/test_trust_hardening.py`; **some were still failing when the previous session's usage was exhausted — the implementation is NOT verified complete; inspect and finish it**

---

## 4. Current verification snapshot (last known state)

```
pytest core/tests backend/tests tests -ra   → 151 passed, 3 skipped
                                              (3 skips = Postgres migration tests; Docker was not running)
strict mypy (CI package list)                → passes (60 source files)
ruff check .                                 → passes
lint-imports                                 → 6 kept, 0 broken
reference-leak guard                         → passes (112 tracked files)
frontend: vitest 5 passed; build passes
git diff --check                             → passes
```

NOT done / NOT claimed: browser E2E (does not exist), live-PostgreSQL verification of current uncommitted work, remote CI for uncommitted work (last remote green run covers Round 2 only).

Verification commands (exact):
```bash
.venv/bin/pytest core/tests backend/tests tests -ra
.venv/bin/mypy core ingestion takeoff classification provenance review boq catalog pricing exports backend
.venv/bin/ruff check .
.venv/bin/lint-imports
.venv/bin/python tools/guards/reference_leak.py
git diff --check
# PostgreSQL tests (docker compose up -d postgres first) — run them if Docker is up; do NOT claim them passed otherwise
```

---

## 5. Git state

- **Pushed HEAD:** `79a2181` (docs: Round 2 exit criterion closed)
- **ALL Round 3 work is uncommitted.** Modified: `Makefile`, `backend/app/api/{auth,projects}.py`, `backend/app/main.py`, `backend/tests/test_migrations.py`, `core/units/geometry_units.py`, `docs/domain-model.md`, `docs/reports/latest-status.md`, `docs/reports/project-progress.md`, `ingestion/dxf/__init__.py`, `pyproject.toml` (types-shapely), `takeoff/rules/__init__.py`, `tests/unit/test_reference_leak_guard.py`. Untracked: everything in the §2 table.
- **Do not reset or discard this work.** Verify, finish the trust gate, then commit in meaningful ticket-sized commits and push.
- Commit trailer: `Co-Authored-By: Claude Code <noreply@anthropic.com>`

---

## 6. REMAINING ROUND 3 WORK — trust-hardening gate (work in this exact order)

The 9 confirmed defects are documented with reproductions in `docs/reports/round-3-report.md` §"Confirmed defects and risks still open". Priorities from the Round 3 continuation prompt:

1. **Wall pairing hardening** — disjoint faces must NEVER pair merely because infinite supporting lines are close (finite congruent longitudinal support required); ambiguous candidates refused not greedily paired; result must be order-independent. (Much appears implemented in `takeoff/wall_detection.py` — verify via tests.)
2. **Evidence enforcement** — empty `source_handles` must never yield MEASURED; both/one/malformed evidence → blocking. (Appears implemented in `engine.py` — verify.)
3. **Replay digest content-binding** — digest must change with geometry coordinates, source identity/version, scale, units, rule/version, relevant params; stay deterministic across ordering. (Appears implemented via `sheet_geometry` snapshots in constants — verify.)
4. **Unsupported DXF geometry** — circles/arcs/bulged polylines/OCS/elevation-3D/nested INSERT/MINSERT/clipping must be explicitly REFUSED/flagged, never reinterpreted as fake line geometry. (KNOWN DEFECT: a circle currently becomes a 2-point polyline. Likely partially fixed — check `ingestion/dxf/__init__.py` warning paths.)
5. **Parser warning propagation** — warnings must reach the measurement boundary; a sheet with unsupported geometry must not look like a complete authoritative takeoff. (Appears implemented via `measure_parsed` — verify.)
6. **Scale/sheet validation** — reject missing/zero/negative/NaN/Infinity factors, mismatched sheet identity, unknown units. Preserve human confirmation.
7. **BOQ/export trust** — measurement state checks, durable measurement identity (`measurement_id` via uuid5 of digest exists), duplicate reference rejection, export blockers. Only what Round 3 contracts require — do not build the full approval system.
8. **Architecture enforcement** — engine bypassing registered rule callable (`run_rule` exists; engine also calls it — check), boq→takeoff import direction vs docs, whether the 6 import contracts actually enforce the documented graph. Strengthen guard tests intentionally; do not casually reorganize.
9. **Foundation follow-up (where practical, don't let it distract)** — project authorization scoping, lifespan registration with the app, job concurrency/idempotency real-DB tests.

**First action in the new chat:** run `.venv/bin/pytest tests/unit/test_trust_hardening.py -q` and fix whatever still fails, then expand coverage for items 4–7 above.

### Explicitly OUT OF SCOPE until the trust gate is complete
Browser E2E · full API/browser integration · full frontend workflow · PDF workflow · raster workflow · catalog expansion · AI classification implementation · 3D viewer · DWG/RVT/IFC · full room detection · full floor engine · complete opening deductions · advanced pricing.

### Round 3 exit criteria
Wall pairing can't invent disjoint connectivity · ambiguous pairings refused/reviewed · evidence enforced · replay identity content-bound · unsupported geometry explicitly refused · parser warnings propagate · scale/sheet validation hardened · BOQ/export trust boundaries enforced to Round 3 level · architecture regressions addressed · all feasible tests pass · skips/blockers honestly documented. Then update: `docs/reports/round-3-report.md`, `latest-status.md`, `project-progress.md`, `tool-availability.md` (with the Tool/Available/Works/Used/Replacement/Notes table), plus the NEXT ROUND HANDOFF block at the bottom of round-3-report.md.

---

## 7. Orchestration constraints (latest instructions)

- **MAXIMUM 3 agents total** (this supersedes the earlier 6-agent Bifrost model — rate limits).
- Lead owns: wall pairing + evidence + replay (tightly coupled), integration, contracts, migrations, final merge/validation/reports.
- Worker 1: DXF unsupported geometry, parser warning propagation, scale/sheet validation.
- Worker 2: BOQ/export trust, architecture guard regressions, foundation audit.
- Workers may READ shared docs/code; never two agents editing the same file. Don't claim agents ran unless they actually ran.
- Regression-first: do not weaken tests to make implementations pass; do not remove working code without evidence.

## 8. Environment quirks (persistent)

- **Classifier outages** (z-ai rate limit, "temporarily unavailable") intermittently block Bash/Agent tools; Write/Read unaffected. Workaround: retry between bursts, use Write directly, and honestly report deviations in round reports. Do NOT hammer a failed tool; record it and continue.
- Docker Desktop sometimes not running — `open -a Docker`, wait, `docker compose up -d postgres` for DB tests; if unavailable, the 3 migration tests skip legitimately (report honestly).
- Log downloads from GitHub API need admin rights — use the public runs/jobs endpoints (`/actions/runs`, `/runs/{id}/jobs`) for CI status, and clean-checkout `git worktree` reproduction for CI debugging.

## 9. Where things are documented

- Round contracts (frozen, additive-only changes via Lead): `docs/domain-model.md`, `docs/api-contract.md`
- Planning: `docs/ticket-backlog.md` (update ticket states there), `docs/implementation-roadmap.md`, `docs/agent-plan.md`
- Reports (update every round): `docs/reports/round-N-report.md`, `latest-status.md`, `project-progress.md`, `tool-availability.md`
- Memory file (kept in sync): `~/.claude/projects/-Users-priyanshuranjankumar-Documents-prj-boq-v2/memory/boq-v2-project-state.md`

**In a new chat: read this file, then `docs/reports/round-3-report.md`, then start at §6 item 1.**
