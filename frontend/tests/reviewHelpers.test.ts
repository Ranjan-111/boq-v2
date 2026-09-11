import { describe, expect, it } from "vitest";
import {
  ELEMENT_TYPES,
  actorShort,
  distinctSubjectTypes,
  effectiveValue,
  filterBySubjectType,
  groupElementsByMeasurements,
  isCorrected,
  isHumanSet,
  isReviewable,
  quantityDisplay,
  sortAuditRows,
} from "../src/lib/reviewHelpers";
import { correctedBadge, typeSourceBadge } from "../src/lib/statusBadges";
import type { AuditEntryRow, Measurement } from "../src/lib/apiClient";

function measurement(overrides: Partial<Measurement> = {}): Measurement {
  return {
    id: "row-1",
    measurement_id: "durable-1",
    element_id: "element-1",
    quantity_type: "length",
    value: "4.000000",
    corrected_value: null,
    unit: "m",
    rule_id: "wall.centerline.length.v1",
    state: "measured",
    label: "Wall 1",
    element_label: "Wall 1",
    element_type: "wall",
    type_source: "geometry_deterministic",
    centerline: null,
    thickness: null,
    evidence: [],
    ...overrides,
  };
}

describe("effectiveValue / isCorrected", () => {
  it("bills the engine value when no correction exists", () => {
    const m = measurement();
    expect(effectiveValue(m)).toBe("4.000000");
    expect(isCorrected(m)).toBe(false);
  });

  it("bills the corrected value when a correction exists", () => {
    const m = measurement({ corrected_value: "6.000000" });
    expect(effectiveValue(m)).toBe("6.000000");
    expect(isCorrected(m)).toBe(true);
  });

  it("never treats the original value as a correction", () => {
    // corrected_value null but value present — original is the truth.
    expect(isCorrected(measurement({ value: "6.000000" }))).toBe(false);
  });
});

describe("quantityDisplay", () => {
  it("plain display for uncorrected rows: original + unit, no pill", () => {
    const d = quantityDisplay(measurement());
    expect(d.original).toBe("4.000000");
    expect(d.corrected).toBeNull();
    expect(d.unit).toBe("m");
    expect(d.correctedPill).toBe(false);
  });

  it("corrected display keeps BOTH numbers and raises the pill", () => {
    const d = quantityDisplay(measurement({ corrected_value: "5.000000" }));
    expect(d.original).toBe("4.000000"); // still visible (strikethrough in UI)
    expect(d.corrected).toBe("5.000000");
    expect(d.correctedPill).toBe(true);
  });
});

describe("isReviewable", () => {
  it("allows measured, measured_zero, needs_review", () => {
    expect(isReviewable("measured")).toBe(true);
    expect(isReviewable("measured_zero")).toBe(true);
    expect(isReviewable("needs_review")).toBe(true);
  });

  it("refuses blocked and not_measurable", () => {
    expect(isReviewable("blocked")).toBe(false);
    expect(isReviewable("not_measurable")).toBe(false);
  });
});

describe("badges", () => {
  it("corrected pill uses the violet accent class", () => {
    expect(correctedBadge().label).toBe("corrected");
    expect(correctedBadge().className).toContain("bg-violet-50");
  });

  it("type_source badge: human_set amber, ai blue, geometry gray", () => {
    expect(typeSourceBadge("human_set").className).toContain("bg-amber-50");
    expect(typeSourceBadge("ai_classified").className).toContain("bg-blue-50");
    expect(typeSourceBadge("geometry_deterministic").className).toContain("bg-ink-100");
  });
});

describe("ELEMENT_TYPES / isHumanSet", () => {
  it("mirrors the backend ElementType vocabulary", () => {
    expect([...ELEMENT_TYPES]).toEqual([
      "wall",
      "room",
      "slab",
      "door",
      "window",
      "opening",
      "floor_finish",
      "other",
    ]);
  });

  it("only human_set counts as a human override", () => {
    expect(isHumanSet("human_set")).toBe(true);
    expect(isHumanSet("geometry_deterministic")).toBe(false);
    expect(isHumanSet("ai_classified")).toBe(false);
  });
});

describe("groupElementsByMeasurements", () => {
  it("groups measurements into distinct elements, counts kept", () => {
    const wall1 = measurement();
    const wall1b = measurement({ id: "row-2", value: "0.920000", quantity_type: "area" });
    const room = measurement({
      id: "row-3",
      measurement_id: "durable-3",
      element_id: "element-2",
      element_label: "Office",
      element_type: "room",
      type_source: "human_set",
    });
    const groups = groupElementsByMeasurements([wall1, wall1b, room]);
    expect(groups).toEqual([
      {
        element_id: "element-1",
        label: "Wall 1",
        element_type: "wall",
        type_source: "geometry_deterministic",
        measurement_count: 2,
      },
      {
        element_id: "element-2",
        label: "Office",
        element_type: "room",
        type_source: "human_set",
        measurement_count: 1,
      },
    ]);
  });

  it("survives an empty measurement list", () => {
    expect(groupElementsByMeasurements([])).toEqual([]);
  });
});

describe("audit row helpers", () => {
  const rows: AuditEntryRow[] = [
    {
      id: "a",
      at: "2026-09-11T10:00:00Z",
      action: "correct_quantity",
      actor: "11111111-2222-3333-4444-555555555555",
      subject_type: "measurement",
      subject_id: "s1",
      project_id: "p1",
      before: null,
      after: null,
      reason: "one",
    },
    {
      id: "b",
      at: "2026-09-11T11:00:00Z",
      action: "override_element_type",
      actor: "11111111-2222-3333-4444-555555555555",
      subject_type: "element",
      subject_id: "s2",
      project_id: "p1",
      before: null,
      after: null,
      reason: "two",
    },
    {
      id: "c",
      at: "2026-09-11T11:00:00Z",
      action: "accept_measurement",
      actor: "99999999-2222-3333-4444-555555555555",
      subject_type: "measurement",
      subject_id: "s3",
      project_id: "p1",
      before: null,
      after: null,
      reason: "three",
    },
  ];

  it("sortAuditRows orders newest first, id DESC on at ties", () => {
    const sorted = sortAuditRows(rows);
    expect(sorted.map((r) => r.id)).toEqual(["c", "b", "a"]);
  });

  it("filterBySubjectType keeps the filter strict ('' = all)", () => {
    expect(filterBySubjectType(rows, "")).toHaveLength(3);
    expect(filterBySubjectType(rows, "element").map((r) => r.id)).toEqual(["b"]);
    expect(filterBySubjectType(rows, "boq")).toEqual([]);
  });

  it("distinctSubjectTypes lists what the page carries, first-seen order", () => {
    expect(distinctSubjectTypes(rows)).toEqual(["measurement", "element"]);
  });

  it("actorShort renders the first 8 chars", () => {
    expect(actorShort(rows[0].actor)).toBe("11111111");
  });
});
