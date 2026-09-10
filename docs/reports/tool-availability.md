# Tool Availability — Current Checkpoint

**Verified:** 2026-09-10 (Round 3 trust-hardening gate) · **Environment:**
local macOS workspace.
Historical routing/classifier errors below belong to earlier sessions and
must not be treated as current tool status.

| Capability | Actual result this checkpoint |
|---|---|
| Shell/read/edit | Working; tests, Docker orchestration, isolated-tree guard probes executed |
| Test isolation | Architecture guard tests copy the source tree into tmp dirs and inject violations there — the user's repo is never mutated (extends the reference-leak guard fix) |
| Python suite | **300 passed, 0 skipped** including live-PostgreSQL migration/authorization tests (was: 151 passed, 3 always-skipped DB tests; the job queue still has no dedicated live-DB test) |
| PostgreSQL | Docker Desktop opened + container `boqv2-postgres-1` started; `docker compose up` hung on first attempt (old exited container present) — starting the existing container directly worked; pg healthy on 5432 |
| Ruff / mypy | ruff clean; mypy strict 66 files clean |
| Architecture | import-linter **9 contracts** kept, 0 broken (6 → 9 this session; each new contract proven to bite via violation-injection tests) |
| Subagents | **2 worker agents ran** (limit 3 per orchestration constraint) — Worker 1 (DXF refusals/warning paths), Worker 2 (BOQ/export + foundation audit); findings folded into round-3-report.md |
| Frontend | 5 Vitest tests passed; TypeScript/Vite build passed |
| Remote CI | **Two green runs on the Round 3 push** (34422918941 code 5/5 jobs; 34423132950 docs-only) after a clean-worktree reproduction that caught 2 pre-push failures; vulnerability scans still not run |
| Browser E2E | Not run; product path not wired (Round 4 scope) |

Installed versions checked this round: ezdxf 1.4.4, Shapely 2.1.2,
types-shapely 2.1.0.20260728, mypy 2.3.1, pytest 9.1.1, import-linter 2.15,
grimp 3.17, SQLAlchemy (asyncpg driver). PyMuPDF absent from the local
virtualenv; spot check, not a full dependency audit.

Tool notes worth retaining:
- `lint-imports` CLI takes `--config`, not `-c`; `python -m importlinter`
  has no `__main__` (guard test learned this the honest way).
- External forbidden modules require top-level
  `include_external_packages = true` in `[tool.importlinter]` (and TOML
  booleans are lowercase `true`).
- Docker Desktop first `docker compose up -d` can hang when an old exited
  container exists; `docker ps -a` + `docker start <container>` is the fast
  path.

See [round-3-report.md](round-3-report.md) for the trust-gate verification
matrix and handoff.

---

## Historical report — prior Round 3 session (retained verbatim)

# Tool Availability Report — Round 3 Start

**Date:** 2026-09-09 · **Session model:** `tokenrouter/z-ai/glm-5.3-free` (Bifrost-routed)
**Purpose:** Live multi-agent capability check requested by the project owner before Round 3 implementation. This was an *actual* test, not a capability assertion — every claim below is backed by a launched attempt in this session.

## Verdict

**True subagent parallelism is UNAVAILABLE in this session.**

The Agent tool cannot launch working subagents end-to-end. Two independent failure layers were isolated; the hard failure is in the Bifrost routing layer, and it is model-independent (all tested model IDs failed identically).

## What was actually launched (attempt log)

| # | Type | Model / path | Result | Exact error |
|---|------|--------------|--------|-------------|
| 1 | Explore | default (`claude-opus-5`) | ❌ FAILED | `API Error: 400 could not auto resolve a provider for the request, please specify a provider explicitly (error type unknown, HTTP 400, model sent to the API: claude-opus-5)` |
| 2 | Explore | default (`claude-opus-5`) | ❌ FAILED | identical |
| 3 | Explore | `model: haiku` → `claude-haiku-4-5-20251001` | ❌ FAILED | identical (model ID changed, error unchanged) |
| 4 | ocx agent | `ocx-gpt-5-4-mini` → `claude-ocx-native--gpt-5.4-mini` | ❌ FAILED | identical (ocx proxy rewrites model string; same 400) |
| 5–8 | fork | parent model (inherited) | ⛔ BLOCKED PRE-LAUNCH | permission-classifier timeout (see below) — never reached the Agent tool |

No agent completed. No parallel execution occurred. The 4 failed agents were sequential-in-effect: each died on its first API call, ~10s after launch.

## Root-cause analysis

**Layer 1 — Bifrost provider mapping (HARD failure, root cause).**
Every subagent request carries a `claude-*` model ID. Bifrost has no provider bound to any `claude-*` string: `claude-opus-5` (Explore/Plan default), `claude-haiku-4-5-20251001` (explicit `model: haiku`), and `claude-ocx-native--gpt-5.4-mini` (ocx proxy rewrite) all returned the same `400 could not auto resolve a provider`. The failure is model-independent within the tested space. The main session works because its model string, `tokenrouter/z-ai/glm-5.3-free`, *is* mapped. Not a rate-limit and not a classifier issue — plain provider mapping.

**Layer 2 — Claude Code permission classifier (TRANSIENT, secondary).**
While testing, the harness's auto-mode permission classifier (backed by the same GLM rate-limited account, 8 req/min — the documented R2 env quirk) timed out. This blocked the fork diagnostics AND the lead's own Bash calls — they never reached the Agent tool or Bifrost at all. It is a separate layer from Bifrost routing and not the root cause of the agent failures. It also means the fork path (inherit-parent-model, the one untested path that could have survived Layer 1) could not be cleanly isolated during the test window: 4 attempts (2 concurrent with other work, 2 isolated) were all blocked pre-launch by the classifier. The fork path remains **unverified**, not disproven.

## Fixes (outside this session)

1. **Bifrost config:** add a provider for `claude-*` model IDs (e.g. map `claude-*` → GLM or add Anthropic keys), or configure Claude Code's `ANTHROPIC_MODEL`/subagent model to the routable `z-ai/glm-5.3-free` string. Any one mapped `claude-*` ID would have let attempts 1–4 succeed.
2. **Classifier:** fix GLM rate limiting (keys/accounts) or bypass the permission classifier in auto mode.

Fixing either layer alone is insufficient: with only Layer 1 fixed, the classifier will still throttle concurrent-agent bursts; with only Layer 2 fixed, subagents still 400 at Bifrost.

## Tools available to the lead (verified this session)

| Tool | Status | Evidence |
|------|--------|----------|
| Read  | ✅ working | read all 8 Round 3 files + settings.json |
| Write | ✅ working | wrote `/tmp/boq-v2-capability-test/scratch.txt` (non-project file) |
| Edit  | ✅ working | edited the scratch file |
| Bash  | ⚠️ intermittent | worked at test start (timestamps, `git status`); blocked by classifier during the test window |
| Agent  | ❌ broken (as lead) | 4 launch attempts, 4 Bifrost 400s; see Layer 1 |

## Consequences for Round 3

Per the project's binding rule ("never fake tests; never mark DONE without verification"), I did not claim subagent parallelism in any Round 3 artifact. Round 3 implementation continues **single-threaded via Write/Read/Edit**, with Bash attempted between classifier recovery windows. This report documents the deviation from the intended multi-agent workflow, per the same honesty standard applied in R2 reports.

## Post-test Round 3 progress under these constraints (same session)

Despite the Agent tool being unavailable, the vertical-slice work continued via the single-executor fallback:

- **Tests written & passing (54 new):** `tests/unit/test_kernel.py` (13), `test_wall_detection.py` (14), `test_engine.py` (8), `test_assembly.py` (11), `tests/integration/test_vertical_slice.py` (1, E2E) + 7 pre-existing DXF-parser tests now counted. Suite: **147 passed, 3 skipped** on the CI-equivalent invocation (`pytest core/tests backend/tests tests`).
- **Real bugs found & fixed by those tests:** (1) `wall_detection.py` overlap branch was dead code — `overlaps` could never populate; replaced with a second-pass scanner with consumed-edge semantics. (2) `are_offset_pair` crashed on zero-length edges via `unit()`; now returns False, degenerate edges surface as unmatched. (3) `apply_markup` returns the markup AMOUNT, not base+markup — `boq/assembly.py` now adds base + markup. (4) `_fmt_money_minor` printed `850` instead of `850.00`; quantized.
- **New modules:** `boq/assembly.py` (T085 slice — unit reconciliation refuses mismatch, invariant-5 recompute check), `exports/csv_export.py` (T100 slice — byte-deterministic CSV; exports→boq import removed via structural Protocol to keep import-linter 6/6).
- **Guards at time of writing:** ruff clean, import-linter 6/6 kept, 147/147 tests. **Pending (Bash-classifier outage):** `pip install types-shapely` (pinned in pyproject `geo` extra) + mypy strict re-run — the shapely stub error is the only known open CI risk.

## Round 3 state files read directly during this test

`takeoff/kernel.py` (71 lines, complete), `tests/unit/test_dxf_parser.py` (124 lines, 14 passing tests), `takeoff/engine.py`, `takeoff/wall_detection.py`, `core/geometry/__init__.py`, `ingestion/dxf/__init__.py`, `takeoff/rules/__init__.py`, `core/units/geometry_units.py`, `docs/reports/tool-availability.md` (absent) — as substitution for the inspection tasks the failed agents were assigned.
