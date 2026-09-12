/**
 * BOQ build guidance — state-aware explanation when a run has no measured
 * quantities to bill (backend refusal `no_measured`).
 *
 * The raw backend message ("run has no measured quantities to build a BOQ
 * from") is true but not actionable. The REAL cause is one of a few honest
 * states, each with its own next step: scale never confirmed, parse
 * incomplete, measurements awaiting review, blocked by exceptions, or
 * nothing measurable. This module derives the explanation from the run's
 * own rows — it never guesses and never invents a quantity.
 */

/** The minimum the guidance needs from the run's rows (both already-fetched
 * list endpoints return these shapes). */
export interface GuidanceMeasurement {
  state: string;
}

export interface GuidanceException {
  code: string;
  severity: string;
  resolved_at: string | null;
}

/** The state-aware next step for a zero-measured run. Null when the run
 * DOES have measured quantities (nothing to explain). */
export function noMeasuredGuidance(
  measurements: GuidanceMeasurement[],
  exceptions: GuidanceException[],
): string | null {
  const measured = measurements.filter(
    (m) => m.state === "measured" || m.state === "measured_zero",
  );
  if (measured.length > 0) return null;

  if (measurements.length === 0) {
    // Nothing was measured at all — the run's own exceptions say why.
    const open = exceptions.filter((e) => !e.resolved_at);
    const blocking = open.filter((e) => e.severity === "blocking");
    if (open.some((e) => e.code === "scale_unconfirmed")) {
      return "This run has no measured quantities because the sheet's scale was never confirmed. Confirm the scale on the Drawings tab, then run takeoff again.";
    }
    if (open.some((e) => e.code === "parse_incomplete")) {
      return "This run has no measured quantities because the drawing could not be fully parsed. Check the drawing's warnings on the Drawings tab and re-upload a corrected file.";
    }
    if (blocking.length > 0) {
      return `Resolve the blocking exception first — this run has no measured quantities because ${blocking.length} blocking exception${blocking.length > 1 ? "s" : ""} stopped takeoff (Runs tab → Exceptions).`;
    }
    return "This run measured nothing — run takeoff again from a drawing whose scale is confirmed (Runs tab).";
  }

  // Measurements exist but none is in an authoritative state.
  const reasons: string[] = [];
  const needsReview = measurements.filter((m) => m.state === "needs_review");
  if (needsReview.length > 0) {
    reasons.push(
      `accept the reviewed candidate${needsReview.length > 1 ? "s" : ""} first — ${needsReview.length} measurement${needsReview.length > 1 ? "s are" : " is"} awaiting review (Runs tab)`,
    );
  }
  const blocked = measurements.filter((m) => m.state === "blocked");
  if (blocked.length > 0) {
    reasons.push(
      `resolve the blocking exception${blocked.length > 1 ? "s" : ""} first — ${blocked.length} measurement${blocked.length > 1 ? "s are" : " is"} blocked (Runs tab → Exceptions)`,
    );
  }
  const notMeasurable = measurements.filter(
    (m) => m.state === "not_measurable",
  );
  if (notMeasurable.length === measurements.length) {
    return "The drawing produced no billable quantities — every measurement was refused as not measurable. The source drawing has no geometry the rules can measure honestly.";
  }
  if (reasons.length === 0) {
    return "This run has no measured quantities.";
  }
  return `This run has no measured quantities to bill — ${reasons.join("; ")}.`;
}
