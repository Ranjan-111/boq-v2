# Latest Status

**Updated:** 2026-09-08 (end of Round 2) · **Round:** 2 — Foundation ✅ COMPLETE
**Next:** Round 3 — Vertical Slice (DXF → wall quantity → evidence → BOQ row → CSV export)

## Live state

- Backend runs (`make api`, port 8000; dev smoke on 8099) with JWT auth + projects API.
- Postgres 16 in docker compose; Alembic baseline applied (21 tables).
- Job queue + worker operational (SKIP LOCKED, idempotency, retries).
- Frontend shell builds, tests pass, talks to the live API.
- All checks green: 90 tests (91 unit + 3 live-DB integration, merge counting) — see note; ruff, mypy strict, import-linter 6/6, reference-leak guard.
- **CI green on GitHub**: run 34273889351 — all 5 jobs (lint, architecture, tests, license-scan, frontend) after fixing a .gitignore rule that had silently excluded `backend/app/storage/` from the repo.

## Round 2 quick facts

- 19/19 attempted tickets DONE or DONE-partial (3 partials with explicit defers).
- 5 substantive commits. Reports in `docs/reports/round-2-report.md`.

## What starts Round 3

T030 DXF parser (ezdxf + dxf.handle capture — the provenance differentiator),
T031 sheets, T032 PDF, T034 scale detection (PROPOSED-only), T040–T042
geometry kernel + wall detection, T049 exceptions engine, T070 evidence,
T111 upload UI. Exit: vertical slice E2E green.
