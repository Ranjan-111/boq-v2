/**
 * Upload/parse progress staging — pure functions (unit-tested in
 * tests/uploadStages.test.ts).
 *
 * HONESTY CONTRACT: no percentage is ever shown unless the backend reported
 * it (Job.progress). Parse jobs report no intermediate progress (0 until
 * complete sets 100), so the UI shows stage-based indeterminate progress —
 * stages derive ONLY from real job state (status + started_at), never from
 * elapsed-time guesses.
 */

export type UploadStage =
  | "idle" // nothing in flight
  | "uploading" // the POST is in flight
  | "queued" // job created, no worker has claimed it yet
  | "parsing" // worker claimed it (status running / started_at set)
  | "complete" // job succeeded, parse ok
  | "failed"; // terminal failure (job failed OR parse refused inside it)

/** Human labels for each stage. */
export const STAGE_LABELS: Record<UploadStage, string> = {
  idle: "Idle",
  uploading: "Uploading",
  queued: "Queued",
  parsing: "Parsing",
  complete: "Complete",
  failed: "Failed",
};

/** The ordered pipeline shown to the user. */
export const STAGE_ORDER: UploadStage[] = [
  "uploading",
  "queued",
  "parsing",
  "complete",
];

/**
 * Derive the current stage from REAL state only:
 *  - job status (queued/running/succeeded/failed/cancelled)
 *  - whether the worker has claimed it (started_at set — a queued job with
 *    no worker sits forever; this distinguishes "waiting" from "working")
 *  - the job's own result for the succeeded case (execute_parse RETURNS
 *    failures {ok: false} instead of raising, so the job row says
 *    "succeeded" while the parse itself failed)
 */
export function deriveStage(state: {
  uploadInFlight: boolean;
  jobId: string | null;
  jobStatus?: string;
  jobStartedAt?: string | null;
  jobResultOk?: boolean | null;
}): UploadStage {
  if (state.uploadInFlight) return "uploading";
  if (state.jobId === null) return "idle";
  switch (state.jobStatus) {
    case undefined:
      return "queued"; // accepted, first job fetch in flight
    case "queued":
      return "queued";
    case "running":
      return state.jobStartedAt ? "parsing" : "queued";
    case "succeeded":
      return state.jobResultOk === false ? "failed" : "complete";
    case "failed":
    case "cancelled":
      return "failed";
    default:
      return "queued";
  }
}

/**
 * A job stuck waiting: queued (never claimed) longer than this is honestly
 * a "no worker is processing jobs" situation, NOT slow parsing. The bar
 * keeps animating (nothing has failed), but the hint tells the user what to
 * check. NOT a timeout — polling continues, nothing is hidden.
 */
export const STUCK_QUEUE_HINT_AFTER_MS = 45_000;

export function isQueueStuck(
  stage: UploadStage,
  jobCreatedAt: string | null | undefined,
  nowMs: number = Date.now(),
): boolean {
  if (stage !== "queued") return false;
  if (!jobCreatedAt) return false;
  const created = Date.parse(jobCreatedAt);
  if (!Number.isFinite(created)) return false;
  return nowMs - created > STUCK_QUEUE_HINT_AFTER_MS;
}

/**
 * Humanize a parse failure reason for the failure card. Parse service
 * messages are machine-prefixed ("parse_failed: DxfParseError: ...") — keep
 * the honest reason but soften library exception noise for the common
 * cases. Never invents a cause; unknown messages pass through unchanged.
 */
export function humanizeParseError(raw: string | null | undefined): string {
  if (!raw) return "The drawing could not be parsed.";
  let msg = raw;
  if (msg.startsWith("parse_failed: ")) msg = msg.slice("parse_failed: ".length);
  const noise: Array<[RegExp, string]> = [
    [/^DxfParseError:\s*ezdxf could not parse:\s*/i, "The DXF file could not be read: "],
    [/^PdfParseError:\s*/i, "The PDF file could not be read: "],
    [/^RasterParseError:\s*/i, "The image could not be read: "],
    [/^stored bytes missing for key .*/i, "The uploaded file is missing from storage — please re-upload it."],
  ];
  for (const [re, replacement] of noise) {
    if (re.test(msg)) return msg.replace(re, replacement);
  }
  return msg;
}
