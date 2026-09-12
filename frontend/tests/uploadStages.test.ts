import { describe, expect, it } from "vitest";

/**
 * Upload/parse stage derivation — the honesty contract of the progress UI:
 * stages come ONLY from real job state (status, started_at, result.ok), a
 * queued-but-unclaimed job is distinguishable from a running parse, and a
 * parse failure INSIDE a succeeded job ({ok:false}) reads as failure.
 */
import {
  STAGE_ORDER,
  STUCK_QUEUE_HINT_AFTER_MS,
  deriveStage,
  humanizeParseError,
  isQueueStuck,
} from "../src/lib/uploadStages";

describe("deriveStage — real state only", () => {
  it("upload in flight beats everything", () => {
    expect(
      deriveStage({ uploadInFlight: true, jobId: null, jobStatus: undefined }),
    ).toBe("uploading");
  });

  it("no job and idle → idle", () => {
    expect(
      deriveStage({ uploadInFlight: false, jobId: null, jobStatus: undefined }),
    ).toBe("idle");
  });

  it("accepted, first fetch in flight → queued (job exists, not yet seen)", () => {
    expect(
      deriveStage({ uploadInFlight: false, jobId: "j1", jobStatus: undefined }),
    ).toBe("queued");
  });

  it("job queued (no worker claimed it) → queued", () => {
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "queued",
        jobStartedAt: null,
      }),
    ).toBe("queued");
  });

  it("running with started_at → parsing (a worker claimed it)", () => {
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "running",
        jobStartedAt: "2026-09-12T10:00:00Z",
      }),
    ).toBe("parsing");
  });

  it("running WITHOUT started_at → queued (claim not visible yet — honest)", () => {
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "running",
        jobStartedAt: null,
      }),
    ).toBe("queued");
  });

  it("succeeded with ok=true → complete", () => {
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "succeeded",
        jobResultOk: true,
      }),
    ).toBe("complete");
  });

  it("succeeded with ok=FALSE (parse refused inside the job) → failed", () => {
    // The parse service returns failures instead of raising: the JOB row
    // says succeeded while the parse itself refused. This is the trap the
    // stage derivation must not miss.
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "succeeded",
        jobResultOk: false,
      }),
    ).toBe("failed");
  });

  it("succeeded with no result yet → complete only when result says ok", () => {
    // result arrives with the same fetch as succeeded; absent result must
    // not claim failure (ok === false is the only failure signal).
    expect(
      deriveStage({
        uploadInFlight: false,
        jobId: "j1",
        jobStatus: "succeeded",
        jobResultOk: null,
      }),
    ).toBe("complete");
  });

  it("job failed / cancelled → failed", () => {
    for (const status of ["failed", "cancelled"]) {
      expect(
        deriveStage({
          uploadInFlight: false,
          jobId: "j1",
          jobStatus: status,
        }),
      ).toBe("failed");
    }
  });

  it("unknown status → queued (never a fabricated stage)", () => {
    expect(
      deriveStage({ uploadInFlight: false, jobId: "j1", jobStatus: "weird" }),
    ).toBe("queued");
  });
});

describe("isQueueStuck — honest no-worker hint, not a timeout", () => {
  const created = new Date("2026-09-12T10:00:00Z").toISOString();
  const at = (s: number) => Date.parse(created) + s * 1000;

  it("a fresh queued job is not stuck", () => {
    expect(isQueueStuck("queued", created, at(5))).toBe(false);
  });

  it("a queued job past the threshold is flagged (no worker)", () => {
    expect(isQueueStuck("queued", created, at(STUCK_QUEUE_HINT_AFTER_MS / 1000 + 5))).toBe(true);
  });

  it("never flags parsing (a claimed job is working, however slow)", () => {
    expect(isQueueStuck("parsing", created, at(1000))).toBe(false);
  });

  it("never flags terminal stages", () => {
    expect(isQueueStuck("complete", created, at(1000))).toBe(false);
    expect(isQueueStuck("failed", created, at(1000))).toBe(false);
  });

  it("no timestamp → never stuck (nothing to compare)", () => {
    expect(isQueueStuck("queued", null, at(1000))).toBe(false);
    expect(isQueueStuck("queued", undefined, at(1000))).toBe(false);
  });

  it("garbage timestamp → never stuck", () => {
    expect(isQueueStuck("queued", "not-a-date", at(1000))).toBe(false);
  });
});

describe("humanizeParseError — honest reasons, softened noise", () => {
  it("strips the parse_failed: prefix", () => {
    expect(humanizeParseError("parse_failed: bad thing")).toBe("bad thing");
  });

  it("softens the common ezdxf DXF failure", () => {
    expect(
      humanizeParseError(
        "DxfParseError: ezdxf could not parse: DXFStructureError: missing EOF tag.",
      ),
    ).toBe("The DXF file could not be read: DXFStructureError: missing EOF tag.");
  });

  it("softens PDF and raster failures", () => {
    expect(humanizeParseError("PdfParseError: no pages found")).toBe(
      "The PDF file could not be read: no pages found",
    );
    expect(humanizeParseError("RasterParseError: cannot decode")).toBe(
      "The image could not be read: cannot decode",
    );
  });

  it("storage-missing becomes an actionable message", () => {
    expect(
      humanizeParseError("stored bytes missing for key uploads/abc"),
    ).toBe("The uploaded file is missing from storage — please re-upload it.");
  });

  it("unknown reasons pass through unchanged (never invented)", () => {
    expect(humanizeParseError("some novel refusal")).toBe("some novel refusal");
  });

  it("null/undefined → generic honest line", () => {
    expect(humanizeParseError(null)).toBe("The drawing could not be parsed.");
    expect(humanizeParseError(undefined)).toBe("The drawing could not be parsed.");
    expect(humanizeParseError("")).toBe("The drawing could not be parsed.");
  });
});

describe("STAGE_ORDER — the pipeline shown to the user", () => {
  it("is the honest ordered pipeline", () => {
    expect(STAGE_ORDER).toEqual(["uploading", "queued", "parsing", "complete"]);
  });
});
