# API Contract — v1.0 (Foundational)

**Status:** Round 1 · Lead-authored. This is **the only API the frontend may see.**
OpenAPI-first: the OpenAPI spec file is the source of truth; this doc is the human contract.

## Conventions

- REST/JSON. Base: `/api/v1`. Auth: `Authorization: Bearer <JWT>` (V1 single workspace).
- All money: integer minor units + ISO currency. All quantities: decimal strings or
  numbers with ≤6 dp; units explicit per field, never implied.
- Errors: RFC 7807 problem+json, machine-readable `code`.
- Async work: 202 + job status endpoints; no long-running HTTP requests.
- Pagination: `?limit&cursor`. Ordering stable by id.
- Every list endpoint accepts `project_id` scoping; cross-project access is 403.

## Projects

```
POST   /projects                     {name, client_name?, region_code, currency}
GET    /projects                     (list)
GET    /projects/{id}
PATCH  /projects/{id}
DELETE /projects/{id}                (soft)
```

## Drawings & sheets

```
POST   /projects/{pid}/drawings          multipart: file (pdf|dxf|png|jpg|webp) → 202 {drawing_file_id, job_id}
GET    /projects/{pid}/drawings           (list with status)
GET    /drawings/{id}                     (metadata + sheets)
DELETE /drawings/{id}
GET    /drawings/{id}/download            (signed URL — storage never public)
GET    /drawings/{id}/sheets
GET    /sheets/{id}                       (page geometry index, calibration status)
GET    /sheets/{id}/tiles/{z}/{x}/{y}     (rendered raster tiles for viewer)
POST   /sheets/{id}/scale/confirm         {units_per_drawing_unit, method:"user_two_point", points:[..]}   ← human gate
```

## Measurement runs

```
POST   /projects/{pid}/runs               {drawing_file_ids:[..], options} → 202 {run_id}
GET    /runs/{id}                          (status, stats, progress)
GET    /runs/{id}/elements                 (filter: sheet_id, element_type, state)
GET    /runs/{id}/measurements             (filter: state, quantity_type, element_id)
GET    /runs/{id}/exceptions               (filter: severity)
POST   /runs/{id}/cancel
```

## Review — evidence, exceptions, corrections

```
GET    /measurements/{id}/evidence          (geometry + source refs for viewer highlight)
POST   /measurements/{id}/review            {action:"accept"|"correct", value?, reason}
                                            → audit row + state transition
POST   /elements/{id}/classification         {element_type, reason}   (human override of AI)
POST   /exceptions/{id}/resolve             {resolution, note}
GET    /projects/{pid}/audit                (filter: subject, actor, since)
```

## Catalogue & rates

```
GET    /catalog/search                     {q, region_code, category?}   (fuzzy + AI-suggested ranking)
GET    /catalog/items/{id}
POST   /catalog/items                       (manual items)
GET    /catalog/items/{id}/rates            (DEFAULT/PROJECT/VENDOR scopes)
PUT    /catalog/items/{id}/rates/{scope}    {amount_minor, currency, vendor?}
GET    /catalog/suggestions?measurement_id= (AI suggestions with confidence — read-only advisory)
```

## BOQ

```
POST   /projects/{pid}/boqs                 {from_run_id}   (build draft BOQ from run)
GET    /projects/{pid}/boqs                 (versions list)
GET    /boqs/{id}                           (full tree: sections + items + totals + mapping refs)
POST   /boqs/{id}/sections                  / PATCH /sections/{id} / DELETE …
POST   /boqs/{id}/items                     {catalogue_item_id, measurement_ids[]?}
PATCH  /boq-items/{id}                      (description, rate override, markup, manual qty)
DELETE /boq-items/{id}
POST   /boqs/{id}/recompute                  (after upstream changes; returns diff)
GET    /boqs/{id}/validation                 (completeness/blocking report)
```

## Approval & export

```
POST   /boqs/{id}/submit                     DRAFT → IN_REVIEW
POST   /boqs/{id}/approve                    {note}   → APPROVED (fails 409 if blockers)
POST   /boqs/{id}/reject                     {note}   → DRAFT
GET    /boqs/{id}/approval-state             (incl. stale flag)
POST   /boqs/{id}/exports                    {format: csv|xlsx|pdf} → 202 {export_id}
GET    /exports/{id}                         (status → artifact URL + manifest)
GET    /exports/{id}/sidecar                 (provenance JSON: every row's provenance chain)
```

## AI endpoints (all advisory, read-only relative to deterministic data)

```
POST   /runs/{id}/analyze                   (kick off AI understanding pass; separate job)
GET    /runs/{id}/ai/insights               (sheet classifications, element classifications,
                                             suggestions, explanations — each with confidence)
POST   /ai/explain                          {subject_type, subject_id}  (on-demand explanation)
```

AI writes go ONLY to `*_suggested` / AI tables. Acceptance of a suggestion is a
review action that records actor + audit row.

## Jobs & events

```
GET    /jobs/{id}                           (upload/parse/run/export job status)
GET    /projects/{pid}/events                (SSE stream: job progress, run completion)
```

## Non-negotiable API rules

1. No endpoint ever lets the client write a computed quantity directly — only
   `review/correct` (audited human correction) may change a quantity.
2. Every quantity-bearing response includes `state` + provenance refs.
3. Scale confirmation is an explicit human POST — parse/auto-detect cannot.
4. Export endpoints verify approval state server-side (409 otherwise).
5. Uploads: virus-scan + format validation + size caps before storage.

## Versioning

Additive-only within v1; breaking changes → `/api/v2` with overlap window.
Deprecations annotated in OpenAPI (`deprecated: true` + sunset header).
