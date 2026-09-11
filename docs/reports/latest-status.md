# Latest Status

**Verified:** 2026-09-12 · **Current:** Round 8 COMPLETE — pushed and
remotely verified green (CI run 34652270146 on `ec99f33`, all seven jobs incl. the MinIO-backed tests job and the docker build job).

Rounds 1–7 remain completed historical milestones. Round 8 delivered the
vector-PDF scale/review depth, an additional deterministic takeoff type,
production storage + deploy composition, and the authz matrix:

- PDF `1:N` text-scale proposals now persist at parse (PROPOSED, exact
  factor, `bar_scale_detected`) — parse still never confirms; the human
  gate does. DXF sheets with no detected units no longer claim a detection
  method (the honesty fix).
- PDF vector candidates surface through the real engine as NEEDS_REVIEW
  measurements (deduplicated against wall-consumed geometry, audited
  accept-only); a BOQ bills an accepted candidate, never a raw one.
- `room.gross.perimeter.v1` — closed-ring perimeter through the same
  kernel refusal gate (engine 0.5.0).
- `S3Storage` boto3 adapter behind the Storage port, verified against live
  MinIO; Dockerfile + prod compose (quay.io MinIO, required secrets without
  defaults) + deploy docs + CI docker-build job; full-stack smoke-tested
  locally (register → project → upload → bytes in the bucket).
- The cross-user authz matrix is pinned: the scale gate refuses strangers
  before any write; three latent defects fixed with regressions (malformed
  id 500s in the last two unguarded resolvers, the login pgproto-UUID 500,
  and the DXF-only `is_modelspace` string compare that hid confirmed PDF
  sheets from the run form).
- The browser journey now has a second test proving the candidate doctrine
  end-to-end (proposal pre-fill → human gate → needs_review → audited
  accept → measured, sibling stays needs_review).

Round 7's completed surfaces remain green beneath:


- CSV, deterministic XLSX, and deterministic PDF export share one
  approval/blocker/evidence/identity gate.
- Successful exports persist a SHA-256-bound provenance JSON sidecar with
  priced rows, measurement identities, source handles, rule/input digests,
  and evidence links; the sidecar is downloadable from the export surfaces.
- Run history, export history, catalogue management, audited catalogue
  mapping, and audited AI suggestion apply are server-backed and ownership
  scoped.
- Raster uploads now parse through the worker into an honest unknown-scale,
  zero-geometry sheet and have an authenticated pixel-only preview. Raster
  pixels never become authoritative quantities.
- The browser E2E service composition is wired into CI, and the complete
  local gated journey is green.

## Verified checks

| Check | Result |
|---|---|
| Python (`core/tests tests/unit tests/integration backend/tests`) | **700 passed, 0 failed, 0 skipped** against live PostgreSQL + live MinIO |
| Frontend Vitest | **79 passed** |
| Frontend build | passed |
| Browser E2E | **2 passed** (DXF correction + PDF candidates) with API, worker, Vite, and PostgreSQL |
| Ruff / strict mypy | clean / clean (108 source files) |
| Import contracts | 9 kept, 0 broken |
| Reference-leak guard | clean (243 tracked files) |
| pip-audit | no known vulnerabilities |
| Docker build + prod compose smoke | passed locally (register → project → upload → bytes in MinIO bucket) |
| Alembic | live database at `a1f4c0d2e9b3 (head)` |

No tests were skipped. The two warnings are both the known JWT
short-secret class (InsecureKeyLengthWarning from the deliberate test
secrets). Remote CI run 34652270146 (`ec99f33`) is green across all seven jobs incl. the MinIO-backed tests job and the docker build job.

## Remaining roadmap

The next milestone is performance profiling against the documented
targets (T125) and the golden-run determinism suite (T121), with the deploy
pipeline (registry push + TLS) as the follow-on. DWG/IFC/RVT ingestion remain post-V1 by the
frozen roadmap. Raster measurement remains deliberately refused until its
human scale and review contract is extended.
