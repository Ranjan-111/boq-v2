# Round 2 Report — Foundation

**Round:** 2 · **Date:** 2026-09-08 · **Objective:** Production foundation for the BOQ V2 product per `docs/ticket-backlog.md` EPIC 1 and `docs/architecture.md`.

## 1. Tickets

| ID | Ticket | Status | Verified by |
|---|---|---|---|
| T010 | Monorepo scaffold + tooling | **DONE** | 12 packages + pyproject + Makefile + docker-compose; venv installs |
| T011 | core/ domain types | **DONE** | typed ids, enums, Money, units; unit tests |
| T012 | core/ state machines | **DONE** | transition tables raise IllegalTransition; unit tests |
| T013 | provenance + exceptions model | **DONE** | records + digest + invariant fn; unit tests |
| T014 | DB models + Alembic + migration tests | **DONE** | baseline migration applied to live PG; 3 integration tests green; roundtrip ok |
| T015 | FastAPI + auth + error envelope | **DONE** | live server: register/login/me/projects; 401 without token; OpenAPI generates |
| T016 | OpenAPI validation vs contract | **PARTIAL** | OpenAPI spec generates (5 paths, 7 schemas); formal spec-diff harness deferred to R3 with the drawings router |
| T017 | Job queue + worker | **DONE** | live submit→claim→process→complete; idempotency dedupe; cancel; FIFO verified |
| T018 | Storage abstraction | **DONE** | port + local adapter (atomic writes, traversal-proof) + memory adapter; unit tests |
| T019 | Upload security | **DONE** (core) | magic-byte sniffing, DXF SECTION/ENTITIES requirement, content-addressed keys; 16 tests. Router wiring + virus-scan hook → R3 |
| T020 | CI + guards | **DONE** (local) | ruff/import-linter/pytest/reference-leak/license-scan workflow authored; all green locally. First remote CI run = on push |
| T021 | Observability | **DONE** (core) | structlog JSON access logs + request ids + /healthz /readyz. Prometheus /metrics → R3+ |

**Blocked/deferred:** none blocked. Deferred with reason: T016 formal harness (needs full route set), T019 router wiring (needs drawings endpoints), T021 metrics (needs deploy ticket).

## 2. Agents & parallelism

Planned 7 lanes; executed under persistent API-classifier outages that blocked **Agent tool launches for most of the round** (and Bash intermittently). Per the round rules ("continue with available lanes rather than idling"), the Lead executed the lanes directly, prioritizing the critical path. Agent attempts: 3 launches retried (DB-migrations, frontend ×2) — all rejected by the classifier; work proceeded single-executor. **This is a deliberate, reported deviation, not silent scope change.** The ownership discipline was preserved (no file had two writers).

## 3. Commits (this round)

| Commit | Content |
|---|---|
| 9c08356 | scaffold + core domain + storage + guards (T010–T013, T018, T020) |
| c468058 | FastAPI app + JWT auth + job queue (T015, T017) |
| 4142435 | Alembic baseline + live API/jobs verification (T014) |
| 6968891 | upload validation security (T019 core) |
| 785b339 | frontend shell (T110 partial) |

## 4. Tests

- **Unit/fast:** 91 tests green (core domain 21, states/money/units/provenance; storage; tokens; upload validation; guard).
- **Integration (Postgres):** 3 migration tests green (21 tables, AI-separation invariant, downgrade/upgrade roundtrip).
- **Live smoke (not mocked):** register→login→me→create/list projects→401-gate; job submit→process→status→cancel; frontend build+preview→proxied login 200.
- **Architecture:** import-linter 6/6 contracts KEPT. Reference-leak guard clean. ruff clean.

## 5. Architecture decisions made this round

1. Python 3.13 + uv; single root pyproject; hatchling packaging.
2. import-linter config lives in pyproject `[tool.importlinter]` (v2 dropped .ini support).
3. B008 (Depends-in-defaults) ignored for the api layer — FastAPI idiom.
4. UUID normalization at boundaries: tokens accept `str | UUID`; API serializers stringify.
5. Lazy session init so the app works without lifespan (tests/dev).
6. Job results JSON serialized explicitly + `CAST(:r AS jsonb)` for asyncpg.
7. Alembic env.py = async engine; migrations are the only schema authority (verified: no create_all anywhere).
8. Frontend error parsing: RFC7807 branch must precede plain-detail branch (found by test).

## 6. Risks / problems encountered

- **API classifier outages:** blocked parallel agents (see §2) and some Bash calls; worked around, ~2h of retries total.
- **asyncpg JSONB binding:** raw `text()` params need explicit JSON casts (fixed, tested).
- **bandit false positives** on `token_type`/test secrets — noqa'd with justification.
- **Reference-leak guard caught its own test** (bait strings) — allowlisted the test explicitly.

## 7. Work intentionally deferred

T016 spec-diff harness · T019 virus-scan hook + router wiring · T021 /metrics · mypy strict gate in CI (mypy config present; full-strict pass scheduled R3 with ingestion code) · SSE events endpoint (R3 with runs) · frontend eslint/prettier configs authored but not yet enforced in CI.

## 8. Exit-criteria verdict

| Criterion | Status |
|---|---|
| foundation builds | ✅ venv install + tsc + vite build |
| migrations work | ✅ live PG upgrade/downgrade/upgrade |
| application starts | ✅ uvicorn serving |
| API contract validates | ✅ OpenAPI generates; manual conformance to api-contract.md |
| auth works | ✅ register/login/me + 401 gates |
| storage works | ✅ validated adapters + tests |
| jobs work | ✅ end-to-end live |
| tests green | ✅ 94 (91 unit + 3 integration) |
| architecture checks green | ✅ 6/6 contracts |
| license/reference guards green | ✅ clean |
| CI passes | ✅ remote run 34273889351: all 5 jobs green (lint, architecture, tests, license-scan, frontend) |
| P0 blockers | ✅ none open |

**Round 2: COMPLETE — CI green** (run 34273889351, 2026-09-09).

## 9. Next tickets (Round 3 — vertical slice)

T030 DXF parser (ezdxf, handle capture) · T031 sheet detection · T032 PDF parser · T034 scale detection · T040 geometry kernel · T041 rules registry · T042 wall detection · T049 exceptions engine · T070 evidence assembly · T111 upload flow UI + T049 review states in API · vertical-slice E2E: DXF → wall quantity → BOQ row → CSV.

## 10. Recommended parallel-agent allocation (Round 3)

- Agent A: `ingestion/dxf` (T030+T031) — Agent B: `ingestion/pdf` (T032+T034) — Agent C: `takeoff` kernel+rules (T040+T041+T042) — Agent D: `backend` runs/drawings routers + jobs wiring (T049+T070) — Agent E: frontend upload+run-progress UI (T111) — Agent F: adversarial fixtures (T120 partial). Lead: contracts, integration, evidence-sidecar path.

## 11. Project completion estimate

Foundation ≈ complete. Remaining: vertical slice (R3, ~1.5–2 wks), full engine (R4), review+AI (R5), BOQ/pricing/approval (R6), exports+hardening (R7). **Overall: ~30–35% of V1 scope done; V1 ETA ≈ 6–8 weeks at current pace** (heavy dependence on classifier availability for parallelism).
