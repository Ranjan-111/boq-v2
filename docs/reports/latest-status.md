# Latest Status

**Verified:** 2026-09-12 · **Current:** Round 9 COMPLETE — pushed and
remotely verified green (CI run 34668532001 on `e811368`, all nine jobs incl. the
perf benchmarks and the GHCR publish; image live at
ghcr.io/ranjan-111/boq-v2:main).

Rounds 1–8 remain completed historical milestones. Round 9 delivered the
measured performance targets, the golden-run determinism suite, and the
deploy pipeline remainder:

- Every architecture.md §G target is now a measured, CI-enforced
  benchmark: DXF 50k parse 1.02s (< 60s), PDF 100 pages 0.26s (< 300s),
  BOQ recompute 5k 0.29s (< 2s — the per-item N+1 fixed, was 6.07s),
  XLSX 5k rows 0.28s (< 10s). Viewer first-paint is the honest gap
  (needs a browser harness).
- Byte-identical golden replay for EVERY committed fixture — 25 goldens
  including honest refusal paths; version-aware drift verdicts
  (same-version drift = unintended, deliberate change = version-bump
  first); deterministic regeneration via `make golden-update`.
- 37 hypothesis property tests (deterministic profile) pin the geometry
  kernel, banker's rounding/pricing, and the replay digest.
- The deploy pipeline is complete up to a real server: caddy TLS profile
  (ACME automatic TLS + HSTS), least-privilege MinIO app user (policy =
  exactly the four object actions on the app bucket — verified live that
  nothing else is possible), and GHCR publishing via the workflow's own
  GITHUB_TOKEN. A latent R8 defect fixed: the worker container showed
  "unhealthy" forever because the image healthcheck probes the API's HTTP
  port, which the worker deliberately does not serve.

Round 8's completed surfaces remain green beneath:

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
| Python (`core/tests tests/unit tests/integration backend/tests`) | **790 passed, 0 failed** against live PostgreSQL + live MinIO (5 perf tests deselected — they run in the perf job) |
| Perf benchmarks (`BOQ_PERF=1`, live PG) | **5 passed in 12.64s** (§G targets measured & asserted) |
| Golden + property suites | 53 golden + 37 property green |
| Frontend Vitest | **79 passed** |
| Frontend build | passed |
| Browser E2E | **2 passed** (DXF correction + PDF candidates) with API, worker, Vite, and PostgreSQL |
| Ruff / strict mypy | clean / clean (108 source files) |
| Import contracts | 9 kept, 0 broken |
| Reference-leak guard | clean (268 tracked files) |
| pip-audit | no known vulnerabilities |
| Docker build + prod compose smoke | passed live (full stack + caddy proxy; least-privilege boq-app storage proof) |
| Alembic | live database at `a1f4c0d2e9b3 (head)` |

The perf suite runs in its own CI job (the only deliberate deselect).
Remote CI run 34668532001 (`e811368`) is green across all nine jobs — the R8
seven plus the perf benchmarks and the GHCR publish.

## Remaining roadmap

The next milestone is real-server deployment against the published
GHCR image (host + DNS are the only missing pieces), the viewer
first-paint benchmark, and the T124/T047/T034 remainders. DWG/IFC/RVT ingestion remain post-V1 by the frozen roadmap.
Raster measurement remains deliberately refused until its human scale and
review contract is extended.
