# Product Vision

**Status:** Round 1 (Discovery + Architecture) · Author: Lead Architect
**Last updated:** 2026-09-08

## One sentence

A modern, AI-native takeoff and BOQ tool where an estimator uploads a
drawing, gets trustworthy quantities with full provenance, reviews only what
needs human judgment, and exports a professional, priced bill of quantities.

## The workflow is the product

```
DRAWING → UNDERSTAND → MEASURE → REVIEW → BOQ → PRICE → APPROVE → EXPORT
```

Everything we build either serves this pipeline or is infrastructure for it.
Nothing else ships.

## Who it's for

- **Primary:** estimators and quantity surveyors at small/mid contractors and
  QS consultancies — currently measuring in Bluebeam/Excel/PDF printouts.
- **Secondary:** contractors who price their own work and need a defensible BOQ
  fast.
- **Not** enterprises needing ERP suites (that market is OCErp's, not ours).

## What "AI-native" means here — and what it never means

| AI does | AI never does |
|---|---|
| Classify a drawing, sheet, element | Invent geometry that isn't in the file |
| Suggest catalogue mappings | Write quantity numbers |
| Explain an exception in plain language | Silently guess scale/units/dimensions |
| Score its own confidence | Hide uncertainty from the reviewer |
| Draft BOQ descriptions | Approve anything |

The deterministic engine measures. AI interprets. Humans decide. The product's
entire trust model rests on this separation, and it is enforced structurally
(AI outputs live in separate tables from measurements; see
`domain-model.md`).

## Why this beats the alternatives

1. **vs. manual takeoff (Excel/Bluebeam):** 10× faster on first pass, same or
   better trust because every number links back to drawing geometry.
2. **vs. OCErp-class ERPs:** zero ERP overhead. Upload → BOQ in minutes, not a
   190-module platform to administer.
3. **vs. pure-AI "magic" tools:** we don't hallucinate quantities. The AI
   proposes; geometry and math produce; humans confirm.

## Trust model (the moat)

- Every quantity carries **provenance**: which drawing entities/sheets it came
  from, which rule computed it, which inputs it used.
- Every correction leaves an **audit trail** entry — original value, new value,
  who, when, why.
- **BLOCKING** exceptions (missing scale, unresolvable geometry, unmapped
  measurement, missing rate) prevent approval/export until resolved.
- Uncertainty is surfaced, never averaged away.

## V1 scope (smallest production-grade release)

- Inputs: **PDF, DXF, raster images**
- Measure: walls (length/area), floors/rooms (area), openings (counts),
  arbitrary length/area/counts with provenance
- AI: drawing understanding, element classification with confidence,
  catalogue suggestions, exception explanations
- Review: drawing↔BOQ two-way highlighting, evidence panel, corrections with
  audit trail, blocker queue
- BOQ: catalogue mapping, rates, markups, totals, sections
- Export: CSV, XLSX, PDF + provenance sidecar (JSON)

V1 explicitly targets one region's catalogue data (India CPWD-derived or
generic starter set — decided in `reuse-matrix.md`) with region-agnostic
plumbing.

## Out of scope for V1 and likely forever

CRM, procurement, scheduling, finance/ERP, HR, field diaries, dashboards
suites, BIM/IFC (later), DWG direct-parse (later, via conversion), multi-currency
FX (later). See `non-goals.md`.

## Open product questions (Round 1 answers required)

1. Single-user or team collaboration in V1? → **Single user + shared projects
   read-only later; concurrency-control deferred.**
2. Regional focus? → **India-first catalogue data, region-agnostic engine.**
3. Pricing model of our product? → **Out of Round 1 scope; architecture keeps
   per-project tenancy from day 1 to make SaaS easy.**
