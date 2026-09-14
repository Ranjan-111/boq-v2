/**
 * Exception guidance mapping — pins the honest classification that drives
 * the ExceptionsPanel: which codes are legitimately resolved by a recorded
 * human decision, and which are source problems whose correct action is
 * elsewhere (confirm scale, set a rate, re-upload). No code may regress
 * into a magic unblock button, and the fallback must never fabricate
 * specifics.
 */
import { describe, expect, it } from "vitest";
import { guidanceFor } from "../src/lib/exceptionGuidance";

/** Every code the backend can emit (core/domain/enums.py). */
const ALL_CODES = [
  "scale_unconfirmed",
  "parse_incomplete",
  "parse_partial",
  "unmapped_measurement",
  "missing_rate",
  "missing_evidence",
  "ambiguous_sheet",
  "self_intersecting",
  "open_polyline",
  "overlap_detected",
  "room_not_enclosed",
  "room_topology",
  "opening_ambiguous",
  "opening_partial_span",
  "pdf_path_unclassified",
  "ai_low_confidence",
  "annotation_skipped",
  "parse_repaired",
] as const;

describe("guidanceFor", () => {
  it("defines full guidance for every known code", () => {
    for (const code of ALL_CODES) {
      const g = guidanceFor(code);
      // every field is non-empty — a reviewer must always see a full story
      expect(g.what.length, `${code}.what`).toBeGreaterThan(10);
      expect(g.consequence.length, `${code}.consequence`).toBeGreaterThan(10);
      expect(g.action.length, `${code}.action`).toBeGreaterThan(10);
      expect(typeof g.humanResolvable, `${code}.humanResolvable`).toBe(
        "boolean",
      );
    }
  });

  it("offers the audited resolve path ONLY for human-decision codes", () => {
    const humanDecision = new Set([
      "unmapped_measurement",
      "parse_partial",
      "self_intersecting",
      "open_polyline",
      "overlap_detected",
      "room_not_enclosed",
      "opening_ambiguous",
      "opening_partial_span",
      "pdf_path_unclassified",
      "ai_low_confidence",
    ]);
    for (const code of ALL_CODES) {
      const g = guidanceFor(code);
      if (humanDecision.has(code)) {
        expect(
          g.humanResolvable,
          `${code} is a human-decision code — resolve offered`,
        ).toBe(true);
      } else {
        expect(
          g.humanResolvable,
          `${code} is a source problem — no resolve button`,
        ).toBe(false);
      }
    }
  });

  it("classifies source problems as not human-resolvable, with their real action", () => {
    // scale confirmation happens on the Drawings tab, not via resolve
    expect(guidanceFor("scale_unconfirmed").humanResolvable).toBe(false);
    expect(guidanceFor("scale_unconfirmed").action).toMatch(/Drawings tab/i);
    // a damaged file is re-uploaded, never "resolved away"
    expect(guidanceFor("parse_incomplete").humanResolvable).toBe(false);
    expect(guidanceFor("parse_incomplete").action).toMatch(/re-upload/i);
    expect(guidanceFor("parse_partial").humanResolvable).toBe(true);
    expect(guidanceFor("parse_partial").action).toMatch(/partial takeoff/i);
    // pricing is fixed in the catalogue, not on the exception
    expect(guidanceFor("missing_rate").humanResolvable).toBe(false);
    expect(guidanceFor("missing_rate").action).toMatch(/Catalogue tab/i);
  });

  it("keeps the AI suggestion advisory — never blocking, never auto-applied", () => {
    const g = guidanceFor("ai_low_confidence");
    expect(g.humanResolvable).toBe(true);
    expect(g.consequence).toMatch(/[Aa]dvisory only/);
    expect(g.action).toMatch(/human judgment/);
  });

  it("explains annotation skips as quantity-neutral (non-blocking)", () => {
    const g = guidanceFor("annotation_skipped");
    expect(g.humanResolvable).toBe(false);
    expect(g.consequence).toMatch(/cannot understate/);
    expect(g.action).toMatch(/Nothing required/);
  });

  it("falls back to generic guidance that never fabricates specifics", () => {
    const g = guidanceFor("code_that_does_not_exist");
    expect(g.what).toContain("An exception was recorded");
    expect(g.action).toMatch(/correct and re-upload|recorded reason/);
    expect(g.humanResolvable).toBe(true);
  });
});
