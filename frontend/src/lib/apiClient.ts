/**
 * Typed API client — fetch wrapper with JWT + unified error parsing.
 *
 * Errors normalize BOTH shapes the backend can emit:
 *  - FastAPI validation/auth: {detail: [{code, message}]} or {detail: string}
 *  - RFC 7807 problem+json:   {type, title, status, code, detail?}
 * into one `ApiError`.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  /** For 409 blockers_present: human-readable blocker strings, else null. */
  readonly blockers: string[] | null;

  constructor(
    status: number,
    code: string,
    title: string,
    message: string,
    blockers: string[] | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.title = title;
    this.blockers = blockers;
  }
}

/** Pull human-readable blocker strings out of a problem+json detail array. */
function extractBlockers(detail: unknown): string[] {
  if (!Array.isArray(detail)) return [];
  return detail.map((d) => {
    if (typeof d === "string") return d;
    if (typeof d === "object" && d !== null) {
      const o = d as Record<string, unknown>;
      if (typeof o.message === "string") return o.message;
      if (typeof o.code === "string") return o.code;
    }
    return JSON.stringify(d);
  });
}const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

function parseError(status: number, body: unknown): ApiError {
  if (typeof body === "object" && body !== null) {
    const b = body as Record<string, unknown>;
    // RFC 7807 problem+json — identified by its machine-readable `code`.
    // Checked BEFORE the FastAPI detail-array branch, because problem+json
    // may also carry a `detail` array (e.g. 409 blockers_present listing the
    // blocking rows).
    if (typeof b.code === "string") {
      const blockers = extractBlockers(b.detail);
      let message: string;
      if (typeof b.detail === "string") message = b.detail;
      else if (blockers.length > 0) message = blockers.join("; ");
      else if (typeof b.title === "string") message = b.title;
      else message = "Request failed";
      return new ApiError(
        status,
        b.code,
        typeof b.title === "string" ? b.title : statusText(status),
        message,
        blockers.length > 0 ? blockers : null,
      );
    }
    // FastAPI validation error: {detail: [{code, message}]}
    if (Array.isArray(b.detail) && b.detail.length > 0) {
      const first = b.detail[0] as Record<string, unknown>;
      return new ApiError(
        status,
        typeof first.code === "string" ? first.code : "error",
        statusText(status),
        typeof first.message === "string" ? first.message : JSON.stringify(first),
      );
    }
    // FastAPI plain string detail
    if (typeof b.detail === "string") {
      return new ApiError(status, "error", statusText(status), b.detail);
    }
  }
  return new ApiError(status, "error", statusText(status), "Request failed");
}

function statusText(status: number): string {
  switch (status) {
    case 400:
      return "Bad request";
    case 401:
      return "Not signed in";
    case 403:
      return "Not allowed";
    case 404:
      return "Not found";
    case 409:
      return "Conflict";
    case 422:
      return "Validation failed";
    case 429:
      return "Too many requests";
    case 503:
      return "Service unavailable";
    default:
      return `Request failed (${status})`;
  }
}

function authHeader(): Record<string, string> {
  const token = localStorage.getItem("boq.token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers: {
        ...authHeader(),
        ...(options.body !== undefined
          ? { "Content-Type": "application/json" }
          : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Network error", "Could not reach the server.");
  }

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) throw parseError(response.status, body);
  return body as T;
}

/**
 * Multipart upload. Deliberately does NOT set Content-Type — the browser
 * must set it (with the correct multipart boundary) itself. Only Authorization
 * is attached.
 */
export async function postForm<T>(path: string, formData: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { ...authHeader() },
      body: formData,
    });
  } catch {
    throw new ApiError(0, "network_error", "Network error", "Could not reach the server.");
  }

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) throw parseError(response.status, body);
  return body as T;
}

// ---------------------------------------------------------------------------
// Money — integer minor units only. NO float math: minor is an integer, and
// dividing by 100 into 2dp is done with integer/string ops so no cent ever
// appears or disappears via binary float rounding.
// ---------------------------------------------------------------------------

/**
 * Format an integer amount of minor units as a decimal string with 2 dp.
 * Pure string/integer arithmetic. Examples: 913750 -> "9137.50", 1 -> "0.01",
 * 0 -> "0.00", -5 -> "-0.05".
 */
export function formatMoney(amountMinor: number): string {
  if (!Number.isInteger(amountMinor)) {
    throw new Error(`formatMoney expects an integer minor amount, got ${amountMinor}`);
  }
  const sign = amountMinor < 0 ? "-" : "";
  const digits = Math.abs(amountMinor).toString(); // integer -> exact digits
  const units = digits.length > 2 ? digits.slice(0, -2) : "0";
  const cents = digits.padStart(2, "0").slice(-2);
  return `${sign}${units}.${cents}`;
}

// ---------------------------------------------------------------------------
// Typed API surface (docs/api-contract.md)
// ---------------------------------------------------------------------------

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}
export interface Project {
  id: string;
  name: string;
  client_name: string | null;
  region_code: string;
  currency: string;
}
export interface ProjectList {
  items: Project[];
  next_cursor: string | null;
}

// --- Drawings & sheets -------------------------------------------------------

/** POST /projects/{pid}/drawings → 202 */
export interface UploadAccepted {
  drawing_file_id: string;
  job_id: string;
}
export type ParseStatus = "pending" | "parsing" | "parsed" | "failed";
export interface DrawingListItem {
  id: string;
  filename: string;
  format: string;
  size_bytes: number;
  sha256: string;
  parse_status: ParseStatus;
  parse_warnings: string[] | null;
  uploaded_at: string;
}
export interface DrawingList {
  items: DrawingListItem[];
}
export interface Calibration {
  status: "proposed" | "confirmed" | "unknown";
  method: string;
  units_per_drawing_unit: string | null;
  confirmed_at: string | null;
}
export interface Sheet {
  id: string;
  sheet_ref: string;
  page_number: number;
  title: string | null;
  sheet_type: string | null;
  is_modelspace: boolean;
  calibration: Calibration | null;
}
/** GET /drawings/{id} */
export interface DrawingDetail {
  id: string;
  project_id: string;
  filename: string;
  format: string;
  parse_status: ParseStatus;
  parse_warnings: string[] | null;
  sheets: Sheet[];
}
export type ScaleMethod = "user_two_point" | "user_known_ratio";
export interface ScaleConfirmBody {
  units_per_drawing_unit: string;
  method: ScaleMethod;
  points?: unknown[];
}
/** POST /sheets/{id}/scale/confirm */
export interface ScaleConfirmed {
  sheet_id: string;
  status: "confirmed";
  method: string;
  units_per_drawing_unit: string;
  confirmed_at: string;
}

// --- Jobs ---------------------------------------------------------------------

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
/** GET /jobs/{id} */
export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  attempts: number;
  progress: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

// --- Runs ---------------------------------------------------------------------

/** POST /projects/{pid}/runs → 202 */
export interface RunAccepted {
  run_id: string;
  job_id: string;
}
export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_exceptions"
  | "failed";
export interface RunStats {
  measured: number;
  blocked: number;
  exceptions: number;
}
/** GET /runs/{id} */
export interface Run {
  id: string;
  project_id: string;
  status: RunStatus;
  stats: RunStats | null;
  error: string | null;
}

// --- Measurements, evidence, exceptions ---------------------------------------

export type QuantityType = "length" | "area" | "count";
export type MeasurementUnit = "m" | "m2" | "mm" | "count";
export type MeasurementState =
  | "measured"
  | "measured_zero"
  | "needs_review"
  | "not_measurable"
  | "blocked";
export interface MeasurementEvidenceRef {
  kind: string;
  ref: string;
  note: string | null;
}
/** One row of GET /runs/{id}/measurements */
export interface Measurement {
  id: string;
  measurement_id: string;
  element_id: string;
  quantity_type: QuantityType;
  value: string;
  corrected_value: string | null;
  unit: MeasurementUnit;
  rule_id: string;
  state: MeasurementState;
  label: string;
  element_label: string | null;
  element_type: string;
  type_source: string;
  centerline: number[][] | null;
  thickness: number | null;
  evidence: MeasurementEvidenceRef[];
}
export interface MeasurementList {
  items: Measurement[];
}
export type ExceptionSeverity = "blocking" | "review" | "info";
export interface ExceptionRow {
  id: string;
  code: string;
  severity: ExceptionSeverity;
  message: string;
  sheet_id: string | null;
  measurement_id: string | null;
  resolved_at: string | null;
  resolution: string | null;
}
export interface ExceptionList {
  items: ExceptionRow[];
}
/** POST /exceptions/{id}/resolve */
export interface ExceptionResolved {
  ok: boolean;
}
export interface SourceHandle {
  format: string;
  sheet_ref: string;
  entity_ref: string;
  layer: string | null;
}
export type GeomType = "line" | "polyline" | "polygon";
export interface EvidenceGeometry {
  geom_type: GeomType;
  coordinates: number[][];
  source_handles: SourceHandle[];
  layer: string | null;
}
/** GET /measurements/{id}/evidence */
export interface EvidenceResponse {
  measurement_id: string;
  state: MeasurementState;
  value: string | null;
  corrected_value: string | null;
  unit: MeasurementUnit | null;
  geometry: EvidenceGeometry[];
  sheet: { id: string; sheet_ref: string };
  /** The element the measurement belongs to (powers the override UI). */
  element: ElementRow | null;
}

// --- Review actions (audited; the only way a quantity changes) ------------------

export type ReviewAction = "accept" | "correct";
/** POST /measurements/{id}/review body */
export interface MeasurementReviewBody {
  action: ReviewAction;
  /** Required for action="correct": the human's number (same unit as the row). */
  value?: string;
  reason: string;
}
/** POST /measurements/{id}/review response */
export interface MeasurementReviewed {
  ok: boolean;
  audit_id: string;
  measurement: Measurement & { corrected_value: string | null };
}
export type ElementType =
  | "wall"
  | "room"
  | "slab"
  | "door"
  | "window"
  | "opening"
  | "floor_finish"
  | "other";
export type ElementTypeSource =
  | "geometry_deterministic"
  | "ai_classified"
  | "human_set";
/** POST /elements/{id}/classification body */
export interface ClassificationBody {
  element_type: ElementType;
  reason: string;
}
/** element payload carried by evidence + classification responses */
export interface ElementRow {
  id: string;
  run_id: string;
  sheet_id: string;
  element_type: ElementType;
  type_source: ElementTypeSource;
  ai_confidence: string | null;
  ai_model: string | null;
  ai_explanation: string | null;
  label: string | null;
}
/** POST /elements/{id}/classification response */
export interface ClassificationReviewed {
  ok: boolean;
  audit_id: string;
  element: ElementRow;
}
/** One row of GET /projects/{pid}/audit */
export interface AuditEntryRow {
  id: string;
  at: string;
  action: string;
  actor: string;
  subject_type: string;
  subject_id: string;
  project_id: string | null;
  before: unknown;
  after: unknown;
  reason: string | null;
}
export interface AuditList {
  items: AuditEntryRow[];
  next_cursor: string | null;
}

// --- BOQ ----------------------------------------------------------------------

export interface BoqListItem {
  id: string;
  version: number;
  status: string;
  from_run_id: string | null;
}
export interface BoqList {
  items: BoqListItem[];
}
/** POST /projects/{pid}/boqs → 201 */
export interface BoqCreated {
  boq_id: string;
  section_count: number;
  item_count: number;
}
export type BoqItemOrigin = "mapped" | "manual" | "pc_sum";
export interface BoqItem {
  id: string;
  origin: BoqItemOrigin;
  catalogue_item_id: string | null;
  code: string;
  description: string;
  unit: string;
  quantity: string;
  rate_minor: number;
  rate_scope: string | null;
  markup_bp: number;
  total_minor: number;
  measurement_ids: string[];
}
export interface BoqSection {
  id: string;
  code: string;
  title: string;
  items: BoqItem[];
}
/** GET /boqs/{id} */
export interface Boq {
  id: string;
  project_id: string;
  version: number;
  status: string;
  sections: BoqSection[];
  totals: { grand_total_minor: number };
}
/** POST submit/approve/reject */
export interface BoqStatusChange {
  status: string;
}

// --- Exports ------------------------------------------------------------------

/** POST /boqs/{id}/exports → 202 */
export interface ExportCreated {
  export_id: string;
  job_id: string;
}
export type ExportStatus = "pending" | "succeeded" | "failed";
/** GET /exports/{id} */
export interface ExportRecord {
  id: string;
  status: ExportStatus;
  sha256: string | null;
  manifest: unknown;
  download_url: string | null;
}

// --- Catalogue -----------------------------------------------------------------

export interface CatalogRateDefault {
  amount_minor: number;
  currency: string;
  scope: string;
}
/** GET /catalog/search row (a score field may also be present — treated as unknown-extra). */
export interface CatalogItem {
  id: string;
  code: string;
  description: string;
  unit: string;
  region_code: string;
  category_path: string;
  default_rate: CatalogRateDefault | null;
}
export interface CatalogList {
  items: CatalogItem[];
}
export interface CatalogRateRow {
  id: string;
  scope: string;
  vendor: string | null;
  currency: string;
  amount_minor: number;
}
export interface CatalogRateList {
  items: CatalogRateRow[];
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", { method: "POST", body: { email, password } }),
  register: (email: string, password: string, display_name: string) =>
    request<TokenResponse>("/auth/register", {
      method: "POST",
      body: { email, password, display_name },
    }),
  me: () => request<AuthUser>("/auth/me"),
  listProjects: () => request<ProjectList>("/projects"),
  createProject: (body: {
    name: string;
    client_name?: string | null;
    region_code: string;
    currency: string;
  }) => request<Project>("/projects", { method: "POST", body }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),

  // Drawings & sheets
  uploadDrawing: (projectId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return postForm<UploadAccepted>(`/projects/${projectId}/drawings`, formData);
  },
  listDrawings: (projectId: string) =>
    request<DrawingList>(`/projects/${projectId}/drawings`),
  getDrawing: (drawingId: string) => request<DrawingDetail>(`/drawings/${drawingId}`),
  confirmScale: (sheetId: string, body: ScaleConfirmBody) =>
    request<ScaleConfirmed>(`/sheets/${sheetId}/scale/confirm`, { method: "POST", body }),

  // Runs
  createRun: (
    projectId: string,
    body: {
      drawing_file_id: string;
      sheet_id: string;
      options: { max_wall_thickness: number };
    },
  ) => request<RunAccepted>(`/projects/${projectId}/runs`, { method: "POST", body }),
  getRun: (runId: string) => request<Run>(`/runs/${runId}`),
  listMeasurements: (runId: string) =>
    request<MeasurementList>(`/runs/${runId}/measurements`),
  listExceptions: (runId: string) => request<ExceptionList>(`/runs/${runId}/exceptions`),
  resolveException: (exceptionId: string, body: { resolution: string; note?: string }) =>
    request<ExceptionResolved>(`/exceptions/${exceptionId}/resolve`, {
      method: "POST",
      body,
    }),
  getEvidence: (measurementId: string) =>
    request<EvidenceResponse>(`/measurements/${measurementId}/evidence`),

  // Review actions (audited — the only way a quantity changes)
  reviewMeasurement: (ref: string, body: MeasurementReviewBody) =>
    request<MeasurementReviewed>(`/measurements/${ref}/review`, {
      method: "POST",
      body,
    }),
  overrideClassification: (elementId: string, body: ClassificationBody) =>
    request<ClassificationReviewed>(`/elements/${elementId}/classification`, {
      method: "POST",
      body,
    }),
  getProjectAudit: (
    projectId: string,
    params: {
      subject_type?: string;
      actor?: string;
      since?: string;
      limit?: number;
      before?: string;
    } = {},
  ) => {
    const pairs: [string, string][] = [];
    if (params.subject_type) pairs.push(["subject_type", params.subject_type]);
    if (params.actor) pairs.push(["actor", params.actor]);
    if (params.since) pairs.push(["since", params.since]);
    if (params.limit !== undefined) pairs.push(["limit", String(params.limit)]);
    if (params.before) pairs.push(["before", params.before]);
    const query = pairs
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join("&");
    return request<AuditList>(
      `/projects/${projectId}/audit${query ? `?${query}` : ""}`,
    );
  },

  // BOQ
  listBoqs: (projectId: string) => request<BoqList>(`/projects/${projectId}/boqs`),
  createBoq: (projectId: string, body: { from_run_id: string }) =>
    request<BoqCreated>(`/projects/${projectId}/boqs`, { method: "POST", body }),
  getBoq: (boqId: string) => request<Boq>(`/boqs/${boqId}`),
  submitBoq: (boqId: string) =>
    request<BoqStatusChange>(`/boqs/${boqId}/submit`, { method: "POST" }),
  reviewBoq: (boqId: string) =>
    request<BoqStatusChange>(`/boqs/${boqId}/review`, { method: "POST" }),
  approveBoq: (boqId: string, note: string) =>
    request<BoqStatusChange>(`/boqs/${boqId}/approve`, { method: "POST", body: { note } }),
  rejectBoq: (boqId: string, note: string) =>
    request<BoqStatusChange>(`/boqs/${boqId}/reject`, { method: "POST", body: { note } }),

  // Exports & jobs
  createExport: (boqId: string, format: "csv") =>
    request<ExportCreated>(`/boqs/${boqId}/exports`, { method: "POST", body: { format } }),
  getExport: (exportId: string) => request<ExportRecord>(`/exports/${exportId}`),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),

  // Catalogue
  searchCatalog: (q: string, regionCode: string) =>
    request<CatalogList>(
      `/catalog/search?q=${encodeURIComponent(q)}&region_code=${encodeURIComponent(regionCode)}`,
    ),
  listCatalogRates: (itemId: string) =>
    request<CatalogRateList>(`/catalog/items/${itemId}/rates`),
  putCatalogRate: (
    itemId: string,
    scope: string,
    body: { amount_minor: number; currency: string; vendor?: string },
  ) => request<CatalogRateRow>(`/catalog/items/${itemId}/rates/${scope}`, {
    method: "PUT",
    body,
  }),
};
