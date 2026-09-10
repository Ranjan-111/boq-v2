import { describe, expect, it } from "vitest";
import {
  boqStatusBadge,
  calibrationBadge,
  exportStatusBadge,
  measurementStateBadge,
  parseStatusBadge,
  runStatusBadge,
  severityBadge,
} from "../src/lib/statusBadges";

describe("parse status badge colors", () => {
  it("pending -> gray, parsing -> blue, parsed -> green, failed -> red", () => {
    expect(parseStatusBadge("pending").className).toContain("bg-ink-100");
    expect(parseStatusBadge("parsing").className).toContain("bg-blue-50");
    expect(parseStatusBadge("parsed").className).toContain("bg-emerald-50");
    expect(parseStatusBadge("failed").className).toContain("bg-red-50");
  });

  it("unknown statuses fall back to gray with the raw label", () => {
    const b = parseStatusBadge("something_new");
    expect(b.className).toContain("bg-ink-100");
    expect(b.label).toBe("something_new");
  });
});

describe("run status badge colors", () => {
  it("maps each run status to its color class", () => {
    expect(runStatusBadge("queued").className).toContain("bg-ink-100");
    expect(runStatusBadge("running").className).toContain("bg-blue-50");
    expect(runStatusBadge("completed").className).toContain("bg-emerald-50");
    expect(runStatusBadge("completed_with_exceptions").className).toContain("bg-amber-50");
    expect(runStatusBadge("failed").className).toContain("bg-red-50");
  });
});

describe("exception severity badge colors", () => {
  it("blocking red, review amber, info gray", () => {
    expect(severityBadge("blocking").className).toContain("bg-red-50");
    expect(severityBadge("review").className).toContain("bg-amber-50");
    expect(severityBadge("info").className).toContain("bg-ink-100");
  });
});

describe("measurement state badge colors", () => {
  it("never renders a quantity without a state-mapped color", () => {
    expect(measurementStateBadge("measured").className).toContain("bg-emerald-50");
    expect(measurementStateBadge("measured_zero").className).toContain("bg-ink-100");
    expect(measurementStateBadge("needs_review").className).toContain("bg-amber-50");
    expect(measurementStateBadge("not_measurable").className).toContain("bg-ink-100");
    expect(measurementStateBadge("blocked").className).toContain("bg-red-50");
  });
});

describe("calibration badge", () => {
  it("confirmed -> green; proposed and unknown -> amber (the human gate)", () => {
    expect(calibrationBadge("confirmed").className).toContain("bg-emerald-50");
    expect(calibrationBadge("proposed").className).toContain("bg-amber-50");
    expect(calibrationBadge("unknown").className).toContain("bg-amber-50");
  });
});

describe("export status badge", () => {
  it("pending blue, succeeded green, failed red", () => {
    expect(exportStatusBadge("pending").className).toContain("bg-blue-50");
    expect(exportStatusBadge("succeeded").className).toContain("bg-emerald-50");
    expect(exportStatusBadge("failed").className).toContain("bg-red-50");
  });
});

describe("BOQ status badge", () => {
  it("covers every state the backend can emit, incl. reviewed + stale", () => {
    expect(boqStatusBadge("draft").className).toContain("bg-ink-100");
    expect(boqStatusBadge("in_review").className).toContain("bg-blue-50");
    expect(boqStatusBadge("reviewed").className).toContain("bg-blue-50");
    expect(boqStatusBadge("approved").className).toContain("bg-emerald-50");
    expect(boqStatusBadge("stale_approved").className).toContain("bg-amber-50");
    expect(boqStatusBadge("exported").className).toContain("bg-emerald-50");
  });
});
