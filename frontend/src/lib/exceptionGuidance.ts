/**
 * Exception guidance — what each run exception MEANS and what to do about it.
 *
 * Pure mapping (unit-tested in tests/exceptionGuidance.test.ts). The
 * ExceptionsPanel renders this instead of a bare code + raw message: a
 * reviewer must be able to understand what is wrong, why it blocks, and the
 * CORRECT next step — where "resolve" is only offered when a recorded human
 * decision is genuinely the resolution. Source-problem exceptions (bad
 * geometry, broken files) say so and point at re-upload, never a magic
 * resolve button that would unblock nothing.
 */

export interface ExceptionGuidance {
  /** Plain-English explanation of what the exception means. */
  what: string;
  /** Why it blocks (or reviews) progress. */
  consequence: string;
  /** The correct user action. */
  action: string;
  /** Whether the audited resolve endpoint is a legitimate path for this
   * code (a recorded human decision), vs. a different action entirely. */
  humanResolvable: boolean;
}

const GUIDANCE: Record<string, ExceptionGuidance> = {
  scale_unconfirmed: {
    what: "The sheet's drawing scale has not been confirmed by a human.",
    consequence:
      "Nothing on this sheet can be measured — quantities would be unanchored to real-world units.",
    action: "Go to the Drawings tab, open the sheet, and confirm its scale (units per drawing unit).",
    humanResolvable: false,
  },
  parse_incomplete: {
    what: "The drawing could not be fully parsed — some entities were refused or the file is damaged.",
    consequence:
      "Refused entities contribute no geometry, so quantities derived from them cannot exist.",
    action:
      "Check the drawing row's warnings on the Drawings tab. If the source file is damaged, re-upload a corrected export of it.",
    humanResolvable: false,
  },
  parse_partial: {
    what: "Some source entities were refused, but other drawing geometry was parsed successfully.",
    consequence:
      "Quantities are available only for the geometry that was actually extracted; the refused entities are excluded.",
    action:
      "Review the refused entity in the drawing warnings. Resolve this notice with a recorded reason if the partial takeoff is acceptable, or upload a corrected export for complete coverage.",
    humanResolvable: true,
  },
  unmapped_measurement: {
    what: "A measured quantity has no catalogue item to bill against.",
    consequence: "The BOQ cannot include this quantity — approval and export stay blocked.",
    action:
      "Map the measurement to a catalogue item in the BOQ tab (Map unmapped), or resolve it with a recorded reason if it should not bill.",
    humanResolvable: true,
  },
  missing_rate: {
    what: "The mapped catalogue item has no price for this project's currency.",
    consequence: "The BOQ line cannot be priced.",
    action: "Set a rate for the catalogue item in the Catalogue tab.",
    humanResolvable: false,
  },
  missing_evidence: {
    what: "A measurement lacks its source-evidence link.",
    consequence: "The quantity cannot be traced to the drawing — no measured row may exist without evidence.",
    action:
      "This indicates a processing fault — re-run the measurement. If it persists, re-upload the drawing.",
    humanResolvable: false,
  },
  ambiguous_sheet: {
    what: "The drawing has multiple sheets and which one to measure is not determinable.",
    consequence: "Measuring the wrong sheet would fabricate quantities.",
    action: "Choose the intended sheet explicitly in the run form.",
    humanResolvable: false,
  },
  self_intersecting: {
    what: "A closed shape crosses itself, so its area is not well-defined.",
    consequence: "An area cannot be honestly computed from a self-crossing boundary.",
    action:
      "Resolve with a recorded reason if the measurement should stand anyway, or correct the geometry in the source file and re-upload.",
    humanResolvable: true,
  },
  open_polyline: {
    what: "A boundary is an open polyline (its ends do not meet).",
    consequence:
      "Review: it may be an intentional gap (a doorway) or an under-drawn boundary. Area rules need closed shapes.",
    action:
      "Review the geometry in the viewer; resolve with a reason if the gap is intentional, or fix the source drawing.",
    humanResolvable: true,
  },
  overlap_detected: {
    what: "Two measured regions overlap.",
    consequence: "Overlapping regions would double-count the same physical area.",
    action: "Review both regions in the viewer; resolve with a recorded reason if the overlap is acceptable.",
    humanResolvable: true,
  },
  room_not_enclosed: {
    what: "A room boundary is not fully enclosed by walls.",
    consequence: "Review: the room's area cannot be bounded without a closed wall ring.",
    action:
      "Check the drawing for missing wall segments; resolve with a reason if the boundary is drawn as intended.",
    humanResolvable: true,
  },
  room_topology: {
    what: "The room/wall adjacency graph is inconsistent (e.g. a wall bounds two non-adjacent rooms).",
    consequence: "Blocking: topology errors make room-level quantities unreliable.",
    action: "Correct the source drawing (wall/room layout) and re-run; this is not a human-decision gap.",
    humanResolvable: false,
  },
  opening_ambiguous: {
    what: "An opening (door/window) could not be attributed to a single wall.",
    consequence: "Review: the opening's width would count against the wrong wall.",
    action:
      "Check the block naming/placement in the drawing; resolve with a reason if the attribution is acceptable.",
    humanResolvable: true,
  },
  opening_partial_span: {
    what: "An opening does not fully span the wall it sits in.",
    consequence: "Review: the net-of-openings deduction would not subtract it cleanly.",
    action:
      "Review the opening geometry; resolve with a recorded reason if it is drawn as intended.",
    humanResolvable: true,
  },
  pdf_path_unclassified: {
    what: "A PDF vector path could not be classified as a measurable element.",
    consequence: "Review: the path is a candidate, never auto-measured.",
    action:
      "The path surfaces as a NEEDS_REVIEW candidate — accept or leave it through the review flow.",
    humanResolvable: true,
  },
  annotation_skipped: {
    what:
      "Annotation entities were skipped (dimensions, hatching, labels, or paper-space layouts).",
    consequence:
      "None for quantities — these entity classes never carry measurable geometry, so their absence cannot understate a measurement.",
    action:
      "Nothing required. The notice is recorded for transparency; the run measured every geometry the rules support.",
    humanResolvable: false,
  },
  parse_repaired: {
    what: "A value-preserving repair was applied while loading the drawing.",
    consequence:
      "None for quantities — the repaired entities were measured; the repair refused and changed nothing.",
    action:
      "Nothing required. The notice names the applied repair (e.g. subclass-marker injection) so the audit trail records how the file loaded.",
    humanResolvable: false,
  },
  ai_low_confidence: {
    what: "An AI suggestion carried low confidence.",
    consequence: "Advisory only — nothing here blocks quantities or changes values.",
    action: "Consider the suggestion with human judgment; apply it only if you agree.",
    humanResolvable: true,
  },
};

const UNKNOWN: ExceptionGuidance = {
  what: "An exception was recorded for this run.",
  consequence: "Review the message below for the specific refusal.",
  action:
    "If the cause is in the source drawing, correct and re-upload it. Otherwise resolve with a recorded reason.",
  humanResolvable: true,
};

/** Guidance for an exception code — always defined, never fabricated specifics. */
export function guidanceFor(code: string): ExceptionGuidance {
  return GUIDANCE[code] ?? UNKNOWN;
}
