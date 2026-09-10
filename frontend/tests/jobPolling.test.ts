import { describe, expect, it } from "vitest";
import {
  isTerminalJobStatus,
  isTerminalRunStatus,
  jobPollIntervalMs,
  runPollIntervalMs,
  JOB_POLL_INTERVAL_MS,
} from "../src/lib/jobPolling";

describe("job poll stop conditions", () => {
  it("stops on succeeded/failed/cancelled, continues on queued/running", () => {
    expect(isTerminalJobStatus("succeeded")).toBe(true);
    expect(isTerminalJobStatus("failed")).toBe(true);
    expect(isTerminalJobStatus("cancelled")).toBe(true);
    expect(isTerminalJobStatus("queued")).toBe(false);
    expect(isTerminalJobStatus("running")).toBe(false);
  });

  it("refetch interval turns false exactly on terminal statuses", () => {
    expect(jobPollIntervalMs("queued")).toBe(JOB_POLL_INTERVAL_MS);
    expect(jobPollIntervalMs("running")).toBe(JOB_POLL_INTERVAL_MS);
    expect(jobPollIntervalMs(undefined)).toBe(JOB_POLL_INTERVAL_MS);
    expect(jobPollIntervalMs("succeeded")).toBe(false);
    expect(jobPollIntervalMs("failed")).toBe(false);
    expect(jobPollIntervalMs("cancelled")).toBe(false);
  });

  it("polls at the spec'd 1.5s cadence", () => {
    expect(JOB_POLL_INTERVAL_MS).toBe(1500);
  });
});

describe("run poll stop conditions", () => {
  it("stops on completed/completed_with_exceptions/failed", () => {
    expect(isTerminalRunStatus("completed")).toBe(true);
    expect(isTerminalRunStatus("completed_with_exceptions")).toBe(true);
    expect(isTerminalRunStatus("failed")).toBe(true);
    expect(isTerminalRunStatus("queued")).toBe(false);
    expect(isTerminalRunStatus("running")).toBe(false);
  });

  it("refetch interval turns false on terminal run statuses", () => {
    expect(runPollIntervalMs("running")).toBe(1500);
    expect(runPollIntervalMs("completed")).toBe(false);
    expect(runPollIntervalMs("failed")).toBe(false);
    expect(runPollIntervalMs(undefined)).toBe(1500);
  });
});

describe("upload flow: 202 accepted then job poll reaches terminal", () => {
  it("a queued->running->succeeded job sequence terminates polling at succeeded", () => {
    // Simulates the DrawingsTab upload flow state machine: POST 202 {job_id}
    // then the poll predicate evaluated per fetched status.
    const statuses = ["queued", "running", "running", "succeeded"] as const;
    let pollCount = 0;
    for (const s of statuses) {
      const interval = jobPollIntervalMs(s);
      if (interval === false) break; // TanStack stops refetching here
      pollCount += 1;
    }
    expect(pollCount).toBe(3); // stops after seeing "succeeded"
    // The drawing list refetch triggers on exactly the terminal statuses.
    expect(isTerminalJobStatus(statuses[statuses.length - 1])).toBe(true);
  });

  it("a failed parse job also stops polling and surfaces honestly", () => {
    expect(jobPollIntervalMs("failed")).toBe(false);
    expect(isTerminalJobStatus("failed")).toBe(true);
  });
});
