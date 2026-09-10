# BOQ V2 — AI-Native Construction Takeoff & Bill of Quantities

A production-grade, AI-native product for estimators, quantity surveyors and
contractors with **one core workflow**:

```
DRAWING → UNDERSTAND → MEASURE → REVIEW → BOQ → PRICE → APPROVE → EXPORT
```

**This is not an ERP.** OpenConstructionERP (OCErp) is used strictly as a
*local, read-only reference* of construction-domain capability. Nothing is
copied from it into this repository without an explicit license review —
see `docs/license-analysis.md` (OCErp is AGPL-3.0, and it stays out of git
entirely; it lives only in `reference/`, which is gitignored).

## Repository layout (target)

```
docs/          Architecture, domain model, API contracts, plans (authored first)
frontend/      Modern web app (upload → viewer → review → BOQ → export)
backend/       API + services (deterministic engines live here)
core/          Shared domain types, measurement states, provenance model
ingestion/     PDF / DXF / raster → normalized geometry with source handles
takeoff/       Deterministic quantity engine (walls, floors, rooms, counts)
classification/ AI classification of elements — never generates numbers
provenance/    Evidence: every quantity traces to drawing geometry
review/        Exceptions, human decisions, audit trail
boq/           BOQ assembly from measurements
catalog/       Catalogue items + mappings
pricing/       Rates, vendor rates, price roll-up
exports/       CSV / XLSX / PDF + provenance sidecar
tests/         Unit, integration, adversarial fixtures, E2E
reference/     LOCAL-ONLY OCErp copy (gitignored, never modified)
```

## Core principles

1. Deterministic engineering calculations stay deterministic.
2. AI may classify, interpret, suggest, explain — **never** invent geometry or
   quantities.
3. Every quantity has provenance back to source drawing geometry.
4. Uncertain results go to human review; **BLOCKING** issues stop
   approval/export.
5. Human corrections preserve an explicit audit trail.
6. Never silently guess scale, units, dimensions or geometry.
7. Dramatically simpler than OCErp. Production quality, not a demo.

## Current status (2026-09-10)

**Round 3 — trust-hardening gate COMPLETE, pushed, remote CI green.**
A working DXF → wall measurements → BOQ → approval-gated CSV library slice
whose trust boundary is adversarially regression-tested end to end: disjoint/
ambiguous walls refused, evidence enforced on every measurement, content-bound
replay identity, unsupported geometry explicitly refused (never faked), scale
human-gated, BOQ from evidenced MEASURED records only, export approval-gated
with fail-closed severity checks, and 9 architecture import contracts proven
to bite by violation-injection guard tests. 300 Python tests (incl. live
PostgreSQL), mypy strict, ruff, import-linter, license guards, frontend build
all green. Round 4 = persistence + API wiring + viewer + browser E2E
(the trust boundary becomes a product). See `docs/reports/`.

Key documents:

- `docs/product-vision.md` — what we are building and for whom
- `docs/architecture.md` — executive architecture decision
- `docs/domain-model.md` — entities, states, provenance contracts
- `docs/api-contract.md` — the only API the frontend may see
- `docs/reuse-matrix.md` — OCErp audit: keep / adapt / rewrite / reject
- `docs/license-analysis.md` — AGPL constraints and safe-path decision
- `docs/implementation-roadmap.md` — timeline to V1 (+ post-V1 capability detail)
- `docs/ticket-backlog.md` — prioritized tickets
- `docs/non-goals.md` — what we will deliberately never build
