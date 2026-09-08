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
| Geo / pointcloud / reality capture | Out for the foreseeable future. |
| Dashboard suites / BI | `bi_dashboards`, `dashboards` out. Our "dashboard" is one project overview page. |
| Regional pack engine (21 packs) | We support regions through catalogue *data*, not through a pack framework with legal/tax rules. |
| GAEB/D81/BCF/COBie format compliance | V1 exports CSV/XLSX/PDF. Exchange formats later if a paying user needs them. |
| Multi-currency FX, price indices, withholding tax | Out. One currency per project in V1. |
| Formwork/rebar/temporary-works specialty modules | Specialized estimation depth — later, only with evidence of demand. |

## Deferred (possible V2+, not in V1)

- DWG input (via external conversion to DXF; we won't license a DWG directXTF SDK in V1)
- IFC input (read-only, quantities from property sets)
- Team collaboration / multi-user editing / approval routing with multiple approvers
- Vendor quote comparison (keep vendor *rates*, defer vendor *management*)
- API keys for external integrations
- Multiple price books / historical rate versioning UI
- Local/embedded AI models

## Tiny dependencies we DO accept despite the "no ERP" rule

- **Users/auth** — a hosted product needs accounts.
- **Background jobs** — takeoff runs are async; needs a job runner.
- **File storage** — drawings are large binaries; needs object storage.
- **Notifications** — only in-app "your run finished" events; no email campaigns.

Anything else that smells like a module from OCErp's 190 gets challenged in
review: "which step of DRAWING→EXPORT does this serve?" — no answer, no merge.
