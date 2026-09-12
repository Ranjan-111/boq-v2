# Round 9 Report — Performance Targets, Golden-Run Determinism, Deploy Pipeline

**Verified:** 2026-09-12 · **Verdict:** Round 9 is COMPLETE — pushed
(`b3cb019..e811368`) and remotely verified green: CI run **34668532001** across
all NINE jobs (lint, architecture, tests, perf, license-scan, docker,
publish, frontend, e2e) — the perf benchmarks ran in CI and the image is
published to GHCR (`tags: main, e811368`). No trust gate was weakened — the
perf benchmarks assert the §G numbers as written, and the golden suite pins
the replay contract harder than any round before.

## What Round 9 completed

- **T125 — Performance (Worker A):** all four measurable architecture.md
  §G targets are now pinned by measured, CI-enforced benchmarks:
  DXF 50k entities parse+normalize **1.02s** (target < 60s), PDF 100 pages
  extract **0.26s** (target < 300s), BOQ recompute 5k items **0.29s**
  (target < 2s), XLSX export 5k rows **0.28s** (target < 10s). The
  `recompute_boq` N+1 (one measurement SELECT per mapped item + one currency
  query per changed item) was the one real defect: 5k items measured
  **6.07s** before the fix (missing the 2s target) → **0.29s** after batch
  loading, with semantics byte-identical (corrected wins, vanished
  identities skipped honestly, same diff/audit shape, DRAFT-only gate
  untouched). cProfile of the 50k parse recorded in-test: hotspots live in
  ezdxf's loader (~83%), not our normalize path — **no ingestion optimization
  made** (the target passes with 58× headroom; measure first, then decide).
  Index decision: **no migration** — EXPLAIN ANALYZE on populated scratch
  DBs (5k rows, then inflated to 505k) shows every hot query index-served at
  ~1–6ms in both regimes. Fixtures are generated at test time (deterministic,
  seeded, never committed — a 6.9 MB DXF and a 100-page PDF build in
  seconds). CI gains a dedicated `perf` job (own PG service, 30-min
  ceiling); the `tests` job deselects via `-m "not perf"`.
  **Honest gap:** viewer < 3s first-paint was NOT measured — it needs a
  browser harness (Playwright budget test is the follow-up).
- **T121 — Golden-run determinism (Worker B):** byte-identical replay for
  EVERY committed fixture — 25 goldens (12 DXF, 7 PDF runs, 6 raster), with
  honest refusals goldened, never skipped (corrupt fixtures record the
  parser's loud refusal; raster fixtures record the engine's BLOCKING
  `scale_unconfirmed` refusal; `twopage.pdf` runs twice, each sheet
  honestly refusing `ambiguous_sheet` under the run-service convention).
  A registry test enforces fixture-set ⇄ golden-set equality. One
  serializer code path produces and compares goldens; values are exact
  Decimal strings; run-level arrays stay in ENGINE order (that order is the
  `element_index` contract); each golden embeds the full replay spec
  (sha256, calibration, units, max_wall_thickness, emit_candidates).
  **Drift verdict is version-aware:** same-version mismatch = UNINTENDED
  (proven by perturbation — a 0.5% length change fails loudly with both
  versions, a unified diff, and the fix path); a deliberate change requires
  bumping ENGINE_VERSION FIRST, then `make golden-update` (regeneration is
  deterministic — twice-run leaves git diff empty; proven). In-process
  double-replay asserted per fixture.
  **Property tests (37, hypothesis):** the deterministic `boq-deterministic`
  profile (derandomized, 3 identical consecutive runs) pins the geometry
  kernel (translation/reversal invariance with documented float64 round-off
  bounds, triangle inequality, isoperimetric bound, refusal determinism for
  bowties/open rings/multi-polygons), banker's rounding + pricing (sign
  symmetry, idempotence, tie-to-even, two-way-split within exactly one
  minor unit, `apply_markup` delta contract), and the replay digest
  (determinism, distinctness-by-construction, sorted-multiset refs).
- **T128 remainder — Deploy pipeline (Lead):**
  - **TLS termination:** caddy `tls` profile — Caddy automatic TLS
    (ACME/Let's Encrypt) for `PUBLIC_DOMAIN` with HSTS + hardened headers;
    `proxy` profile stays plain HTTP for smoke tests. The container refuses
    to start with `CADDY_CONFIG=tls` and no domain (ACME cannot issue for
    localhost — fail fast). Live-verified via the proxy profile (register
    201 through caddy); the tls profile needs a public host by design.
  - **Least-privilege storage:** `minio-init` creates a dedicated `boq-app`
    user whose policy allows ONLY `PutObject/GetObject/DeleteObject` on the
    app bucket — the app never holds MinIO root credentials. Proven live
    end-to-end: register → project → PDF upload → worker parse → bytes
    verified via `boq-app`; list-buckets/create-bucket/put-other-bucket/
    delete-bucket all refused. (`HeadObject` is not a valid MinIO policy
    action — HEAD auth rides on GetObject; discovered live.)
  - **Registry push:** CI `publish` job pushes `ghcr.io/<owner>/<repo>:main`
    + `:<short-sha>` on main pushes using the workflow's own
    `GITHUB_TOKEN` (zero new secrets; explicit `packages: write`
    permission; PRs build only).
  - **Latent R8 defect fixed:** the worker container showed "unhealthy"
    forever — the image HEALTHCHECK probes the API's `:8000/healthz` but
    the worker deliberately serves no HTTP port (it was parsing jobs fine
    the whole time). Worker healthcheck honestly disabled; liveness is the
    process itself.

## Files and modules changed

Worker A: `backend/app/services/boq_service.py` (recompute batch fix),
`tests/perf/{conftest,test_parse_benchmarks,test_recompute_benchmark,
test_xlsx_export_benchmark}.py`, `tests/fixtures/generate_bench_fixtures.py`,
`pyproject.toml` (perf marker), `.github/workflows/ci.yml` (perf job + not-perf
deselect + publish job). Worker B: `tests/golden/{golden_run,test_golden_runs,
conftest}.py`, `tests/golden/data/*.json` (25), `tests/unit/test_property_
{geometry,rounding,digest}.py`, `tests/conftest.py` (hypothesis profile),
`Makefile` (`golden-update`), `docs/testing-strategy.md` (§8 golden doctrine,
§9 property doctrine). Lead: `docker-compose.prod.yml` (boq-app user, tls
profile, worker healthcheck), `docs/deploy.md`, `.env.example`, round docs.

## Verification actually run

| Check | Result |
|---|---|
| Full Python suite (live PG + MinIO, integrated tree) | **790 passed, 0 failed** (5 perf deselected by design) |
| Perf suite (live PG, `BOQ_PERF=1`) | **5 passed in 12.64s** — §G numbers measured & asserted |
| Golden + property suites | 53 golden + 37 property green; hypothesis 3× identical |
| Frontend Vitest / tsc / build | **79 passed** / clean / passed |
| Browser E2E (both journeys, full local stack) | **2 passed (19.9s)** |
| Ruff / strict mypy | clean / clean (108 source files) |
| Import contracts | 9 kept, 0 broken |
| Reference-leak guard | clean (268 tracked files) |
| Prod compose smoke | full stack + caddy proxy + least-privilege proof (live) |
| Compose profile renders | `proxy` and `tls` both validate |
| Alembic | `a1f4c0d2e9b3 (head)`; no migration this round (indexes not needed) |

## Architectural decisions

The perf suite asserts §G targets as written — a slower CI machine failing
honestly is preferred to a weakened gate (headroom: 58×, 1000×, 6.7×, 33×).
The golden suite treats ENGINE ORDER as a pinned contract and Decimal
strings as the only value encoding, so drift can never hide behind
serialization choices. The deliberate-drift path is version-bump-first —
mirroring the persisted-run replay contract. The S3 policy is scoped to
exactly the four object actions the Storage port performs; nothing else,
on nothing but the app bucket.

## Open work and deliberate deferrals

- Nothing from Round 9's own scope — the round is closed with remote CI
  green (the deferrals below are follow-on work, not R9 gaps).
- Viewer < 3s first-paint benchmark (needs a Playwright harness).
- Real-server deployment (host + DNS) — everything up to it is now done:
  image publishes to GHCR, TLS profile exists, least-privilege storage
  verified.
- Rate limits + secrets audit (T124 remainder); count-by-example
  seed-picker UI (T047); scale-bar heuristic (T034).
- DWG/IFC/RVT remain post-V1; raster takeoff stays refused.

## Next milestone

Real-server deployment against the published GHCR image — host + DNS are
the only missing pieces (TLS profile, least-privilege storage, and
publishing all exist and are verified). Otherwise: the viewer first-paint
benchmark, and the T124/T047/T034 remainders.
