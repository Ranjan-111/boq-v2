/**
 * BOQ build guidance — pins the honest state-aware explanations for a
 * `no_measured` refusal. Each real cause has its own next step; the
 * guidance never fabricates a quantity and never claims a run is buildable
 * when its rows say otherwise.
 */
import { describe, expect, it } from "vitest";
import { noMeasuredGuidance } from "../src/lib/boqBuildGuidance";

const m = (state: string) => ({ state });
const ex = (code: string, severity = "review", resolved = false) => ({
  code,
  severity,
  resolved_at: resolved ? "2026-01-01T00:00:00Z" : null,
});

describe("noMeasuredGuidance", () => {
  it("returns null when the run HAS measured quantities", () => {
    expect(
      noMeasuredGuidance([m("measured"), m("measured_zero")], []),
    ).toBeNull();
  });

  it("explains an unconfirmed scale (the most common zero-cause)", () => {
    const out = noMeasuredGuidance([], [ex("scale_unconfirmed", "blocking")]);
    expect(out).toMatch(/scale was never confirmed/);
    expect(out).toMatch(/Drawings tab/);
  });

  it("explains an incomplete parse", () => {
    const out = noMeasuredGuidance([], [ex("parse_incomplete", "blocking")]);
    expect(out).toMatch(/could not be fully parsed/);
    expect(out).toMatch(/re-upload/);
  });

  it("names the blocking exception count when nothing else is specific", () => {
    const out = noMeasuredGuidance(
      [],
      [ex("room_topology", "blocking"), ex("room_topology", "blocking")],
    );
    expect(out).toMatch(/Resolve the blocking exception first/);
    expect(out).toMatch(/2 blocking exceptions/);
  });

  it("tells a bare empty run to take off again", () => {
    const out = noMeasuredGuidance([], []);
    expect(out).toMatch(/run takeoff again/);
  });

  it("directs NEEDS_REVIEW rows to the review flow", () => {
    const out = noMeasuredGuidance(
      [m("needs_review"), m("needs_review")],
      [],
    );
    expect(out).toMatch(/accept the reviewed candidates first/);
    expect(out).toMatch(/2 measurements are awaiting review/);
  });

  it("directs blocked rows to the exceptions queue", () => {
    const out = noMeasuredGuidance([m("blocked")], [ex("missing_evidence")]);
    expect(out).toMatch(/resolve the blocking exceptions? first/);
  });

  it("states when the drawing honestly has nothing billable", () => {
    const out = noMeasuredGuidance(
      [m("not_measurable"), m("not_measurable")],
      [],
    );
    expect(out).toMatch(/no billable quantities/);
  });
});
