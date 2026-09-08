# Project Progress

**Project:** boq-v2 — AI-native takeoff & BOQ · **Repo:** Ranjan-111/boq-v2
**Roadmap:** `docs/implementation-roadmap.md` (10 weeks to V1)

## Round-by-round

| Round | Phase | Status | Commits | Tests | Notes |
|---|---|---|---|---|---|
| 1 | Discovery + architecture | ✅ DONE | 8 | — | 11 docs; 5 parallel audits; licensing policy locked (AGPL: patterns only, no code/data copying; PyMuPDF banned) |
| 2 | Foundation | ✅ DONE | 8 | 90 | Monorepo + core domain + Postgres/Alembic + FastAPI/auth + jobs + storage + upload security + guards + frontend shell; CI green on GitHub (run 34273889351) |
| 3 | Vertical slice | ⏳ NEXT | — | — | DXF→geometry→wall quantity→evidence→BOQ row→CSV, E2E |
| 4 | Full takeoff engine | ⏸ | — | — | Rooms, floors, openings, deductions, PDF/raster candidates |
| 5 | AI + review workspace | ⏸ | — | — | Provider gateway (schema-enforced), classification, suggestions, review UI |
| 6 | BOQ + pricing + approval | ⏸ | — | — | Catalogue, mapping, rates, markups, approval gates |
| 7 | Exports + hardening | ⏸ | — | — | XLSX/PDF/sidecar, perf, security, E2E, deploy |

## Cumulative

- **Commits (project code+docs):** 16
- **Tests:** 90 green in CI (87 unit + 3 live-DB integration; the CI-identical local run also 90)
- **Architecture contracts enforced:** 6 import-linter contracts, CI-configured
- **Guards:** reference-leak ✅ · license-scan ✅ (workflow) · import-linter ✅
- **V1 completion estimate:** ~30–35% (foundation complete; the vertical slice is the next trust milestone)

## Trust invariants implemented so far

1. Scale calibration is human-gated at the type level (`ScaleCalibration.require_confirmed()` raises; engine converts exceptions, never guesses).
2. Money is integer-minor-units with banker's rounding; markup in integer bp.
3. State machines reject illegal transitions (measured/blocked/approval flows).
4. AI suggestions live in a separate table — enforced by migration test (`measurements` cannot carry AI fields).
5. Uploads are magic-byte-validated before storage; content-addressed keys.
6. Every job is idempotent-capable and retry-bounded.
