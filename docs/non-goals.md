# Non-Goals

**Status:** Round 1 · These are commitments, not merely "not now".

## Never building (explicit rejections)

| Area | Why rejected |
|---|---|
| CRM / sales pipeline | OCErp has it (`crm`, `webhook_leads`, `portal`). Not our user, not our workflow. |
| Procurement / POs / goods receipt | Downstream of our export; other tools own it. |
| Construction scheduling / 4D | `schedule`, `schedule_advanced` stay out. BOQ hand-off is our boundary. |
| Finance ERP / invoicing / payroll / EVM | `finance`, `full_evm`, `payroll`, `cvr` all out. |
| HR / teams / credentials | Out. |
| Field diaries / site logs / inspections | `daily_diary`, `fieldreports`, `inspections` out — field is a different product. |
| BIM clash detection / coordination / BCF | `clash`, `coordination_hub`, `bcf` out. IFC import is *input* only, later. |
| Geo / pointcloud / reality capture / 3D Tiles / geospatial visualization | Out for the foreseeable future. The deferred 3D *BIM* viewer (T130) renders building elements, not geospatial scenes. |
| Dashboard suites / BI | `bi_dashboards`, `dashboards` out. Our "dashboard" is one project overview page. |
| Regional pack engine (21 packs) | We support regions through catalogue *data*, not through a pack framework with legal/tax rules. |
| GAEB/D81/BCF/COBie format compliance | V1 exports CSV/XLSX/PDF. Exchange formats later if a paying user needs them. |
| Multi-currency FX, price indices, withholding tax | Out. One currency per project in V1. |
| Formwork/rebar/temporary-works specialty modules | Specialized estimation depth — later, only with evidence of demand. |

## Deferred (possible V2+, not in V1)

- DWG input (via external conversion to DXF — OSS converter or documented user
  export step; we won't license a DWG SDK or a proprietary converter in V1;
  even OCErp's DWG path depends on proprietary x86-64-only converters)
- IFC input (read-only, quantities from property sets)
- 3D/BIM model viewer (render + inspect 3D elements; click BOQ row → highlight
  linked BIM element) — post-V1 candidate (T130), gated on IFC/BIM input
  existing first; the V1 viewer is the 2D drawing review instrument (T112/T113)
- Team collaboration / multi-user editing / approval routing with multiple approvers
- Vendor quote comparison (keep vendor *rates*, defer vendor *management*)
- API keys for external integrations
- Multiple price books / historical rate versioning UI
- Local/embedded AI models

## Pricing-data honesty labels (reconciliation 2026-09-10)

Every rate in the system carries a data-class label — a bundled dataset is
never marketed as live:

- **STATIC** — bundled/hand-authored snapshot with recorded source + date.
- **IMPORTED** — user-uploaded price list (CSV/XLSX) with import timestamp +
  source recorded. The V1 vendor path.
- **DYNAMIC** — on-demand fetch from a recorded external price-source API,
  each fetch recorded per use. Post-V1 only (T095).
- **Real-time vendor pricing — NOT PLANNED.** No reference system provides
  one (OCErp: file-upload price lists, watched-folder connectors, ECB FX as
  its only dynamic feed), and we will not claim it.

## Tiny dependencies we DO accept despite the "no ERP" rule

- **Users/auth** — a hosted product needs accounts.
- **Background jobs** — takeoff runs are async; needs a job runner.
- **File storage** — drawings are large binaries; needs object storage.
- **Notifications** — only in-app "your run finished" events; no email campaigns.

Anything else that smells like a module from OCErp's 190 gets challenged in
review: "which step of DRAWING→EXPORT does this serve?" — no answer, no merge.
