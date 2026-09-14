# Latest Status

**Verified:** 2026-09-14 · **Current:** Round 9 COMPLETE, engine 0.9.0
(junction completion) and the Round 10 trust/UI follow-up implemented and
live-verified — pushed and remotely verified green (latest CI run 34845002372
on `0e8235a`, all nine jobs incl. the
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

## Round 10 follow-up — trust/UI remediation

The follow-up preserves supported geometry when a parser refuses unrelated
entities, emits explicit `parse_partial` review exceptions, and keeps
warning-only or structurally invalid sheets blocking. It also prevents the
test-only AI stub from appearing as a model run, reports missing HTTP model
configuration honestly, and adds a viewer-only source-geometry fallback for
terminal runs with no persisted classified elements. Exact planar DXF `POINT`
entities are preserved as render-only evidence without becoming quantities.
Engine measurement semantics are unchanged from `0.9.0` (the junction slice
commits 8692236/f496fc1); `parse_partial` changes only how parser warnings
gate a run, never a measured value. The single golden that changes under it
(pdf__curves) records the rectangle candidate surfacing as NEEDS_REVIEW
with the parse_partial review exception, generated from the code that is
actually committed (the 8692236-era golden had recorded this state while
its implementation stayed uncommitted — repaired in f496fc1). See
[`round-10-report.md`](round-10-report.md) for the exact files and checks.

The full matrix was rerun 2026-09-14: 692 Python unit tests, 149
integration tests against live PostgreSQL + MinIO, ruff/strict-mypy clean,
import contracts 9/9, tsc + 135 vitest green, and all six browser E2E
journeys green on the full R10 stack — plus remote CI fully green on the
base commit 0e8235a (run 34845002372, nine jobs including e2e).

## Round 9 verified checks (historical)

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

## Round 10 follow-up checks

| Check | Result |
|---|---|
| Python regression suite | Passed; five optional perf tests skipped because `BOQ_PERF=1` was not set |
| Trust + golden regressions | 36 trust-hardening tests and all 25 golden fixtures passed |
| Backend geometry API | Five targeted tests skipped because PostgreSQL was unavailable |
| Frontend Vitest / build | 135 passed / passed |
| Ruff / strict mypy | clean / clean (110 source files) |
| Import contracts | 9 kept, 0 broken |
| Browser E2E | not rerun for this follow-up |

The perf suite runs in its own CI job (the only deliberate deselect).
Remote CI run 34668532001 (`e811368`) is green across all nine jobs — the R8
seven plus the perf benchmarks and the GHCR publish.

## Remaining roadmap

The next milestone is the bulk/grouped exception-resolution UX (the real
drawing's 256-card review queue is the top friction), the dev `/files/{key}`
download route (local:// export links are dead hrefs against LocalStorage),
and the scale-lie mitigation heuristic (T034: surface a suspicion when
drawing text labels contradict `$INSUNITS`), then real-server deployment
against the published GHCR image (host + DNS are the only missing pieces),
the viewer first-paint benchmark, and the T124/T047 remainders. DWG/IFC/RVT
ingestion remain post-V1 by the frozen roadmap. Raster measurement remains
deliberately refused until its human scale and review contract is extended.

## Product journey findings (2026-09-14, Floorplan (1).dxf, live stack)

The real-drawing journey completed end-to-end: 248 priced rows approved and
exported (CSV/XLSX/PDF), money exact to the minor unit (rows sum ==
TOTAL row == API grand total), sha256-verified artifacts, provenance
sidecars, and a complete audit trail (333 exception resolutions, 161
audited catalogue mappings, BOQ transitions, 3 exports). Friction found:

1. **Scale lie** — the DXF header claims mm while the drawing is drawn in
   inches; a ratio-1.0 confirmation produced quantities 25.4× too small
   that looked perfectly sane. Caught only by cross-checking the drawing's
   own room labels against stored values. The scale gate needs a
   labels-vs-header consistency suspicion (T034).
2. **Auto-mapping is structurally impossible on a real drawing** —
   candidates match by unit only, so two m-rule groups (wall length, room
   perimeter) always collide symmetrically and every group needs the human
   mapping path. 324 measurements → 248 human-mapped rows.
3. **256 exception cards** resolved one-by-one is the #1 UX debt; the API
   already permits grouped resolution (approve gate waits for REVIEW rows).
4. **Dev export download** — LocalStorage returns `local://` URLs with no
   serving route; the UI Download links are dead hrefs in dev.
