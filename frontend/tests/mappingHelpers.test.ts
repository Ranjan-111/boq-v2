import { describe, expect, it } from "vitest";
import {
  APPLYABLE_SUGGESTION_TYPES,
  CATALOG_UNITS,
  confidencePercent,
  isApplyableSuggestion,
  majorToMinor,
  suggestionSummary,
  unmappedExceptionMessage,
  pairUnmappedBlockers,
} from "../src/lib/reviewHelpers";
import type { Measurement } from "../src/lib/apiClient";

function measurement(overrides: Partial<Measurement> = {}): Measurement {
  return {
    id: "row-1",
    measurement_id: "durable-1",
    element_id: "element-1",
    quantity_type: "area",
    value: "12.500000",
    corrected_value: null,
    unit: "m2",
    rule_id: "wall.footprint.area.v1",
    state: "measured",
    label: "Wall 1 footprint",
    element_label: "Wall 1",
    element_type: "wall",
    type_source: "geometry_deterministic",
    centerline: null,
    thickness: null,
    evidence: [],
    ...overrides,
  };
}

describe("majorToMinor (the rates API speaks minor units)", () => {
  it("converts a human major-unit decimal to integer minor units", () => {
    expect(majorToMinor("850.00")).toBe(85000);
    expect(majorToMinor("0.01")).toBe(1);
    expect(majorToMinor("4250")).toBe(425000);
  });

  it("rounds half-away from zero via Math.round — never float-truncates", () => {
    // 2.005 * 100 is 200.49999999999997 in binary float; Math.round gives
    // the honest 2.5 → 250 case instead of a silent 2.499…
    expect(majorToMinor("12.345")).toBe(1235); // rounds to nearest
    expect(majorToMinor("0.005")).toBe(1); // half-up, never 0
  });

  it("refuses garbage: empty, non-finite, negative → null", () => {
    expect(majorToMinor("")).toBeNull();
    expect(majorToMinor("   ")).toBeNull();
    expect(majorToMinor("abc")).toBeNull();
    expect(majorToMinor("-5")).toBeNull();
    expect(majorToMinor("1e9")).not.toBeNull(); // finite is fine
  });
});

describe("confidencePercent (weak stub values stay visible)", () => {
  it("renders the stub's 0.05 as an honest 5%", () => {
    expect(confidencePercent(0.05)).toBe("5%");
  });

  it("renders strong and zero confidences without hiding them", () => {
    expect(confidencePercent(0.95)).toBe("95%");
    expect(confidencePercent(0.872)).toBe("87%");
    expect(confidencePercent(0)).toBe("0%");
  });
});

describe("unmappedExceptionMessage (the blocker pairing key)", () => {
  it("rebuilds the exact message the backend's build writes", () => {
    expect(unmappedExceptionMessage(measurement())).toBe(
      "measurement not mapped to any catalogue item: " +
        "Wall 1 footprint (wall.footprint.area.v1 m2)",
    );
  });

  it("falls back to the durable identity when the label is null", () => {
    expect(
      unmappedExceptionMessage(measurement({ label: null })),
    ).toBe(
      "measurement not mapped to any catalogue item: durable-1 " +
        "(wall.footprint.area.v1 m2)",
    );
  });
});

describe("pairUnmappedBlockers", () => {
  const wall1 = measurement();
  const wall2 = measurement({
    id: "row-2",
    measurement_id: "durable-2",
    label: "Wall 2 footprint",
  });

  it("pairs each unresolved unmapped blocker with its measurement row", () => {
    const pairs = pairUnmappedBlockers(
      [
        {
          code: "unmapped_measurement",
          message: unmappedExceptionMessage(wall1),
          resolved_at: null,
        },
        {
          code: "unmapped_measurement",
          message: unmappedExceptionMessage(wall2),
          resolved_at: null,
        },
      ],
      [wall1, wall2],
    );
    expect(pairs.map((p) => p.measurement?.id)).toEqual(["row-1", "row-2"]);
  });

  it("drops resolved blockers and non-unmapped exceptions", () => {
    const pairs = pairUnmappedBlockers(
      [
        {
          code: "unmapped_measurement",
          message: unmappedExceptionMessage(wall1),
          resolved_at: "2026-09-11T10:00:00Z", // already mapped
        },
        {
          code: "overlap_detected",
          message: "unrelated",
          resolved_at: null,
        },
        {
          code: "unmapped_measurement",
          message: unmappedExceptionMessage(wall2),
          resolved_at: null,
        },
      ],
      [wall1, wall2],
    );
    expect(pairs).toHaveLength(1);
    expect(pairs[0].measurement?.id).toBe("row-2");
  });

  it("surfaces an unmatched blocker with measurement=null, never drops it", () => {
    const pairs = pairUnmappedBlockers(
      [
        {
          code: "unmapped_measurement",
          message: "measurement not mapped to any catalogue item: Ghost (x y)",
          resolved_at: null,
        },
      ],
      [wall1],
    );
    expect(pairs).toHaveLength(1);
    expect(pairs[0].measurement).toBeNull();
  });
});

describe("isApplyableSuggestion (the client mirror of the quantity guard)", () => {
  it("allows exactly the classification kind", () => {
    expect(APPLYABLE_SUGGESTION_TYPES).toEqual(["element_classification"]);
    expect(isApplyableSuggestion("element_classification")).toBe(true);
  });

  it("refuses informational and rogue kinds", () => {
    expect(isApplyableSuggestion("exception_explanation")).toBe(false);
    expect(isApplyableSuggestion("quantity_estimate")).toBe(false);
  });
});

describe("suggestionSummary", () => {
  it("reads the classification payload's type and rationale", () => {
    expect(
      suggestionSummary({
        suggestion_type: "element_classification",
        payload: { element_type: "room", rationale: "enclosed boundary" },
      }),
    ).toBe("suggests room — enclosed boundary");
    expect(
      suggestionSummary({
        suggestion_type: "element_classification",
        payload: { element_type: "wall" },
      }),
    ).toBe("suggests wall");
  });

  it("reads the exception explanation; unknown kinds never invent content", () => {
    expect(
      suggestionSummary({
        suggestion_type: "exception_explanation",
        payload: { explanation: "the m2 collision is ambiguous" },
      }),
    ).toBe("the m2 collision is ambiguous");
    expect(
      suggestionSummary({ suggestion_type: "quantity_estimate", payload: {} }),
    ).toBe("advisory suggestion");
  });
});

describe("CATALOG_UNITS", () => {
  it("mirrors the backend MeasurementUnit vocabulary", () => {
    expect([...CATALOG_UNITS]).toEqual(["mm", "m", "m2", "m3", "count"]);
  });
});
