# Multi-Agent Execution Plan

**Status:** Round 1 · How we actually run the agent fleet (not a theoretical plan).

## Contract-first orchestration

```
        LEAD (this session)
         │ 1. writes contracts first
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ ROUND A — PARALLEL AUDITS (read-only over reference/)    │
  │  A architecture · B ingestion/takeoff · C boq/pricing    │
  │  D ai/validation · E licensing      [5 agents, now]     │
  └────────────────────────────────────────────────────────┘
         │ findings merged by Lead into reuse-matrix,
         │ license-analysis, roadmap
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ ROUND B — FOUNDATION (mostly sequential, Lead-owned)     │
  │  repo scaffold · core/ domain types + state machines ·   │
  │  DB schema/migrations · OpenAPI spec · CI skeleton       │
  └────────────────────────────────────────────────────────┘
         │ contracts frozen (versioned; additive changes only)
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ ROUND C — PARALLEL BUILD (agents on separate dirs;       │
  │          no two agents own the same package)             │
  │  B1 ingestion-dxf · B2 ingestion-pdf · B3 ingestion-raster│
  │  C1 takeoff-engine · D1 ai-layer · E1 review/provenance  │
  │  F1 frontend-shell · H1 test-infra · I1 devops            │
  └────────────────────────────────────────────────────────┘
         │ Lead integrates; full suite green
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ ROUND D — PARALLEL PRODUCT LAYER                         │
  │  F2 viewer · F3 review UI · F4 BOQ workspace · F5 export  │
  │  D2 catalogue-suggestions · E2 audit-trail UI ·           │
  │  C2 pricing · H2 adversarial fixtures                      │
  └────────────────────────────────────────────────────────┘
         ▼
  ┌────────────────────────────────────────────────────────┐
  │ ROUND E — HARDENING (parallel)                           │
  │  perf (large DXF) · security review · E2E browser tests  │
  │  observability · deploy pipeline · docs                  │
  └────────────────────────────────────────────────────────┘
```

## Ownership map (anti-conflict rules)

| Package / area | Round C owner | Round D owner |
|---|---|---|
| `core/` | Lead (contract changes only) | Lead |
| `ingestion/` | B1 (dxf) + B2 (pdf) — separate subpackages, shared interfaces from core | B3 (raster) |
| `takeoff/` | C1 | C1 continues |
| `classification/` | D1 | D2 |
| `provenance/`, `review/` | E1 | E2 |
| `boq/`, `catalog/`, `pricing/` | Lead + C2 (rates) | C2 |
| `exports/` | — (interfaces stubbed) | F5 + Lead |
| `frontend/` | F1 shell | F2–F5 (page teams, one route each) |
| `tests/` | H1 infra + fixtures | H2 adversarial suite |
| `.github/`, `deploy/` | I1 | I1 |

Rules enforced by Lead at integration:
1. **Contracts are frozen before Round C.** Changes are PR'd against
   `domain-model.md`/`api-contract.md` first, then propagated.
2. Agents never edit another agent's package; cross-package needs become an
   interface in `core/` (Lead merges).
3. Every agent lands with tests; red suites block integration, not "noted".
4. No agent may reference-import OCErp code; patterns and formats only
   (license-analysis.md).

## Agent prompts used in Round A (recorded for reproducibility)

- **A — architecture:** module loader, dependency graph over 20 key modules'
  manifest `depends`, minimal transitive subset, frontend shell separability,
  sdk/cv-pipeline independence, pack data formats, maturity signals.
- **B — ingestion/takeoff:** cad, dwg_takeoff, takeoff, measurement, markups
  (scale), eac engine (actual algorithms vs plumbing), cv-pipeline, geometry
  libs, provenance patterns in the drawing path.
- **C — boq/pricing:** boq model depth, costs/catalog structure, matching engines
  (exact/semantic split), minimal pricing path, approval engine, export formats,
  pack data (india-cpwd focus) incl. per-pack license/attribution facts.
- **D — ai/validation:** ai vs ai_estimators vs ai_agents capabilities, provider
  abstraction, confidence/guardrail patterns, validation rule structure,
  AI-vs-confirmed value separation.
- **E — licensing:** AGPL §13 mechanics for our 4 reuse strategies, PyMuPDF
  cascade, permissive dependency baseline, pack-data copyright posture, policy
  recommendation for this repo.

## Worktree/branch policy

Round 1 is docs-only on `main`. Round B+ : agents work on
`feat/<area>` branches in the same repo (solo project — worktrees only if two
agents must touch the same package, which the ownership map forbids anyway).

## Definition of done (per round)

- Round A: 10 docs authored, findings reconciled, no unresolved
  contradiction between audits (or explicitly documented + decided).
- Round B: `core/` types + state machines unit-tested; Alembic baseline; OpenAPI
  validates; CI runs lint+import-linter+tests on commit.
- Round C: vertical slice demo — DXF in → geometry → one quantity class →
  measured+evidenced → shown on drawing → one BOQ row → export CSV. E2E green.
- Round D: full V1 workflow usable by a real estimator on sample drawings.
- Round E: perf targets met, security checklist closed, deploy script produces a
  running production instance.
