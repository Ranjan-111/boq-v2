# Round 8 Report — Vector-PDF Depth, Deploy Storage, and the Authz Matrix

**Verified:** 2026-09-12 · **Verdict:** Round 8 implementation scope is
complete locally. The R7 architecture was extended in place: candidates
flow through the existing NEEDS_REVIEW review surface, the S3 adapter
sits behind the existing Storage port, and no trust gate was weakened.

## What Round 8 completed

- **Vector-PDF scale/review depth (Worker A, T034/T047 slices):** the PDF
  `1:N` text-scale proposal is now wired into parse persistence (PROPOSED +
  `bar_scale_detected` + exact factor — parse still never confirms), and the
  PDF vector candidates surface through the real engine as NEEDS_REVIEW
  measurements via the existing `polygon.area.v1` / `polyline.length.v1`
  rules — never MEASURED, deduplicated against wall-consumed geometries,
  accepted only through the audited human review flow. A BOQ bills an
  accepted candidate; its unaccepted siblings stay out. Count candidates
  are deliberately deferred (they need a human-picked seed).
- **Parse honesty fix:** a DXF sheet with NO detected units no longer
  claims `method=detected_from_dxf_units` with a NULL factor — method is
  written only when a proposal exists (the old row was a lie of
  attribution).
- **`room.gross.perimeter.v1` (additional deterministic takeoff type):**
  closed-ring perimeter through the same kernel refusal gate as area;
  emitted beside room gross area with the same element/evidence. Engine
  version 0.5.0 (old runs replay by their own stamped version).
- **PDF drawing-unit composition:** a CONFIRMED PDF calibration is the
  complete physical ratio (the bar-scale factor bakes the point's own
  25.4/72 mm into units_per_drawing_unit), so the drawing-unit→mm base is
  the identity — documented in `measure_parsed`; verified values: 1:100 →
  factor 35.2777777778, the fixture's 300 pt polyline → 10.583333 m.
- **Production storage + deploy (Worker B, T128 first slice):** `S3Storage`
  boto3 adapter behind the Storage port — verified against live MinIO
  (presigned URLs fetch their bytes over plain HTTP, declared-length
  enforced before any network I/O, idempotent delete, lazy import so
  local mode boots without boto3). Dockerfile (multi-stage, non-root,
  healthchecked, migrations-then-uvicorn CMD), prod compose (Postgres +
  MinIO + api + worker + optional Caddy profile; required secrets have no
  defaults — compose refuses to start without them), and deploy docs. The
  full stack was smoke-tested locally end-to-end: register → project →
  PDF upload → the exact bytes verified inside the MinIO bucket. A
  build-only `docker` job joined CI.
- **MinIO left Docker Hub** (the `minio/minio` repo is deleted; MinIO now
  publishes exclusively on quay.io): dev compose, prod compose, and CI all
  moved to `quay.io/minio/minio`; CI starts it with an explicit
  `docker run` + readiness loop (GH service containers cannot override
  the quay image's help-printing default CMD).
- **Authz matrix (Lead, T124):** THE scale gate is pinned cross-user safe —
  a stranger can neither read another user's sheet nor confirm scale on
  it (404 before any write; calibration untouched; zero audit rows), owner
  confirms are audited project-scoped, and the confirm body refuses
  nonpositive/nonfinite/over-precise factors and machine methods.
- **Three latent defects found and fixed with regressions:**
  (a) `_owned_sheet` and `_owned_drawing` — the last resolvers without the
  uuid-guard — answered 500 (asyncpg cast) instead of 404 for malformed
  ids; (b) the login endpoint re-SELECTs the user, so its id is the
  asyncpg pgproto UUID — the response model 500s on it (register's
  in-session str id hides the bug; found by the deploy smoke test, pinned
  by a register-then-login roundtrip); (c) `is_modelspace` was derived
  from a DXF-only `sheet_ref == "modelspace"` string compare in
  `get_drawing`/`get_sheet` — every PDF page (a drawing sheet by
  definition) was hidden from the run form even with confirmed scale. The
  honest persisted `sheet_type == "plan"` (written by the parser from its
  own modelspace flag) is the derivation now; the new E2E journey pins it.
- **Browser E2E (Lead):** a second journey proves the R8 doctrine
  end-to-end — upload scale-annotated PDF → parse → the "SCALE 1:100"
  proposal PRE-FILLS the confirm form (35.2777777778) → THE human gate →
  the run emits candidates as needs_review (badge text asserted, never
  measured) → the audited Accept flips exactly one candidate to measured
  while its sibling stays needs_review → the audit trail shows the
  acceptance.

## Files and modules changed

Worker A: `takeoff/{engine,kernel,rules/__init__}.py`,
`backend/app/services/{parse_service,run_service}.py`,
`tests/fixtures/generate_pdf_fixtures.py` +
`tests/fixtures/pdf/scale_annotation.pdf` (hand-authored, committed),
`tests/unit/{test_full_engine,test_kernel,test_pdf_candidate_emission}.py`,
`backend/tests/test_pdf_review_depth.py`. Worker B:
`backend/app/storage/{base,s3}.py`, `pyproject.toml` (s3 extra),
`Makefile`, `Dockerfile`, `.dockerignore`, `docker-compose.prod.yml`,
`docs/deploy.md`, `.github/workflows/ci.yml` (MinIO step + docker job),
`backend/tests/test_s3_storage.py`. Lead: `backend/app/api/{sheets,
drawings,auth}.py` (guards + honest is_modelspace + login str()),
`backend/tests/{test_security_authz,test_scale_api}.py`,
`docker-compose.yml` (quay.io), `frontend/e2e/gated-journey.spec.ts`
(second journey), round docs.

## Verification actually run

| Check | Result |
|---|---|
| Full Python suite (live PostgreSQL + live MinIO) | **700 passed, 0 failed, 0 skipped** |
| Backend suite alone | 194 passed (12 S3 tests incl.) |
| Pure suites (core/tests + tests/unit + tests/integration) | 494 passed |
| Frontend Vitest | **79 passed** |
| Frontend TypeScript/Vite build | passed |
| Browser E2E (both journeys, full local stack) | **2 passed (19.5s)** |
| Ruff / strict mypy | clean / clean (108 source files) |
| Import contracts | 9 kept, 0 broken |
| Reference-leak guard | clean (232 tracked files) |
| pip-audit | no known vulnerabilities |
| Docker image build | succeeded (locally; CI `docker` job mirrors it) |
| Prod compose smoke test | full stack up; register → project → upload → bytes in MinIO bucket |
| Alembic | `a1f4c0d2e9b3 (head)` on the live dev DB; no migration this round |

Remote CI execution remains the open gate — the push runs the same
matrix in CI (including the new MinIO step and docker build job).

## Architectural decisions

Candidates are emitted through the SAME registered rules and digest
machinery as authoritative measurements (state override NEEDS_REVIEW) —
so acceptance is an ordinary audited review action, not a new path. The
S3 adapter enforces LocalStorage's declared-length contract locally
before any network I/O (a short/overlong stream never stores anything).
`is_modelspace` is now derived from the parser's persisted modelspace
flag, not a format-specific string. PDF drawing-units compose as mm
identity because the confirmed calibration already carries the full
point→mm ratio.

## Open work and deliberate deferrals

- Remote CI run of this round's push (the standing round gate).
- Count-by-example candidates need a human seed-picker UI (deferred).
- DWG/IFC/RVT ingestion remain post-V1 by the frozen roadmap (T037 P2).
- Raster takeoff remains refused (human scale + review contract first).
- Registry push, TLS termination, real-server deployment remain open.
- Perf targets (T125: 50k-entity parse < 60s, 5k-row recompute < 2s)
  remain unmeasured.

## Next milestone

Verify remote CI green, then the remaining hardening tickets in
priority order: performance profiling against the documented targets
(T125) and the golden-run determinism suite (T121), with the deploy
pipeline (registry + TLS) as the follow-on.
