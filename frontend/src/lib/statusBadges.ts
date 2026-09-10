/**
 * Status → badge color/label mapping. Pure functions (unit-tested in
 * tests/statusBadges.test.ts). Tailwind classes are complete strings so the
 * JIT content scanner picks them up.
 */

export interface Badge {
  label: string;
  /** Tailwind classes for the badge chip. */
  className: string;
}

const GRAY = "rounded bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium text-ink-600";
const BLUE = "rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700";
const GREEN = "rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700";
const RED = "rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700";
const AMBER = "rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700";

/** Drawing parse_status badge: pending gray, parsing blue, parsed green, failed red. */
export function parseStatusBadge(status: string): Badge {
  switch (status) {
    case "pending":
      return { label: "pending", className: GRAY };
    case "parsing":
      return { label: "parsing", className: BLUE };
    case "parsed":
      return { label: "parsed", className: GREEN };
    case "failed":
      return { label: "failed", className: RED };
    default:
      return { label: status, className: GRAY };
  }
}

/** Run status badge. */
export function runStatusBadge(status: string): Badge {
  switch (status) {
    case "queued":
      return { label: "queued", className: GRAY };
    case "running":
      return { label: "running", className: BLUE };
    case "completed":
      return { label: "completed", className: GREEN };
    case "completed_with_exceptions":
      return { label: "completed w/ exceptions", className: AMBER };
    case "failed":
      return { label: "failed", className: RED };
    default:
      return { label: status, className: GRAY };
  }
}

/** Exception severity badge: blocking red, review amber, info gray. */
export function severityBadge(severity: string): Badge {
  switch (severity) {
    case "blocking":
      return { label: "blocking", className: RED };
    case "review":
      return { label: "review", className: AMBER };
    case "info":
      return { label: "info", className: GRAY };
    default:
      return { label: severity, className: GRAY };
  }
}

/**
 * Measurement state badge — used on EVERY quantity row so no quantity is ever
 * displayed without its state. Not-measurable/blocked/needs_review states get
 * honest "not trusted as-is" colors.
 */
export function measurementStateBadge(state: string): Badge {
  switch (state) {
    case "measured":
      return { label: "measured", className: GREEN };
    case "measured_zero":
      return { label: "measured (zero)", className: GRAY };
    case "needs_review":
      return { label: "needs review", className: AMBER };
    case "not_measurable":
      return { label: "not measurable", className: GRAY };
    case "blocked":
      return { label: "blocked", className: RED };
    default:
      return { label: state, className: GRAY };
  }
}

/** Calibration status badge (proposed = awaiting the human gate). */
export function calibrationBadge(status: string): Badge {
  switch (status) {
    case "confirmed":
      return { label: "scale confirmed", className: GREEN };
    case "proposed":
      return { label: "scale proposed — needs confirmation", className: AMBER };
    default:
      return { label: "scale unknown", className: AMBER };
  }
}

/** BOQ status badge. */
export function boqStatusBadge(status: string): Badge {
  switch (status) {
    case "draft":
      return { label: "draft", className: GRAY };
    case "in_review":
      return { label: "in review", className: BLUE };
    case "reviewed":
      return { label: "reviewed", className: BLUE };
    case "approved":
      return { label: "approved", className: GREEN };
    case "stale_approved":
      return { label: "stale (inputs changed)", className: AMBER };
    case "exported":
      return { label: "approved · exported", className: GREEN };
    default:
      return { label: status, className: GRAY };
  }
}

/** Export record status badge: pending blue, succeeded green, failed red. */
export function exportStatusBadge(status: string): Badge {
  switch (status) {
    case "pending":
      return { label: "pending", className: BLUE };
    case "succeeded":
      return { label: "ready", className: GREEN };
    case "failed":
      return { label: "failed", className: RED };
    default:
      return { label: status, className: GRAY };
  }
}
