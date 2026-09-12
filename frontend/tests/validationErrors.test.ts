import { describe, expect, it, vi, beforeEach } from "vitest";

/**
 * Humanized validation errors — the raw-JSON-in-the-UI bug: Pydantic 422s
 * arrive as {detail: [{type, loc, msg, input, ctx}]} and must NEVER surface
 * verbatim. Each common shape maps to a plain sentence; anything unmapped
 * gets a generic-but-honest line, never the internal structure.
 */
import { ApiError, api } from "../src/lib/apiClient";

function mockFetch(status: number, body: unknown) {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
  );
}

const storage = new Map<string, string>();
vi.stubGlobal(
  "localStorage",
  {
    getItem: (k: string) => storage.get(k) ?? null,
    setItem: (k: string, v: string) => void storage.set(k, v),
    removeItem: (k: string) => void storage.delete(k),
    clear: () => void storage.clear(),
  } as Storage,
);

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

describe("pydantic 422 errors are humanized, never raw JSON", () => {
  it("invalid email → plain-English sentence, no JSON, no msg field", async () => {
    // The exact shape the backend returns for "fkuy@ghf" (reproduced live).
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          {
            type: "value_error",
            loc: ["body", "email"],
            msg: "value is not a valid email address: The part after the @-sign is not valid. It should have a period.",
            input: "fkuy@ghf",
            ctx: {
              reason: "The part after the @-sign is not valid. It should have a period.",
            },
          },
        ],
      }),
    );
    const err = await api
      .register("fkuy@ghf", "password-1", "T")
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(422);
    expect(apiErr.code).toBe("validation_error");
    expect(apiErr.message).toBe(
      "Please enter a valid email address, e.g. name@example.com.",
    );
    // The raw structure NEVER reaches the UI message.
    expect(apiErr.message).not.toContain("{");
    expect(apiErr.message).not.toContain("value_error");
    expect(apiErr.message).not.toContain("loc");
  });

  it("too-short password names the field and the minimum", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          {
            type: "string_too_short",
            loc: ["body", "password"],
            msg: "String should have at least 8 characters",
            input: "short",
            ctx: { min_length: 8 },
          },
        ],
      }),
    );
    const err = await api
      .register("a@b.dev", "short", "T")
      .catch((e: unknown) => e);
    expect((err as ApiError).message).toBe(
      "The password is too short — at least 8 characters.",
    );
  });

  it("too-long display name names the maximum", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "display_name"],
            msg: "String should have at most 200 characters",
            ctx: { max_length: 200 },
          },
        ],
      }),
    );
    const err = await api
      .register("a@b.dev", "password-1", "x".repeat(201))
      .catch((e: unknown) => e);
    expect((err as ApiError).message).toBe(
      "The display name is too long — at most 200 characters.",
    );
  });

  it("pattern mismatch (e.g. region code) → generic field message", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          {
            type: "string_pattern_mismatch",
            loc: ["body", "region_code"],
            msg: "String should match pattern '^[A-Za-z0-9-]+$'",
            input: "IN!",
          },
        ],
      }),
    );
    const err = await api
      .createProject({ name: "x", region_code: "IN!", currency: "INR" })
      .catch((e: unknown) => e);
    expect((err as ApiError).message).toBe(
      "The region format is not valid — please check it and try again.",
    );
  });

  it("missing field → 'fill in the …'", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [{ type: "missing", loc: ["body", "name"] }],
      }),
    );
    const err = await api
      .createProject({ name: "", region_code: "IN", currency: "INR" })
      .catch((e: unknown) => e);
    expect((err as ApiError).message).toBe("Please fill in the name.");
  });

  it("unmapped pydantic type → honest generic line, never the structure", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          {
            type: "some_new_future_type",
            loc: ["body", "mystery"],
            msg: "whatever",
            ctx: { exotic: { nested: true } },
          },
        ],
      }),
    );
    const err = await api.login("a@b.dev", "password-1").catch((e: unknown) => e);
    expect((err as ApiError).message).toBe(
      "Some entries are not valid yet — please check the form and try again.",
    );
    expect((err as ApiError).message).not.toContain("{");
    expect((err as ApiError).message).not.toContain("some_new_future_type");
  });

  it("multiple errors → the first is shown (arrays are not dumped)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(422, {
        detail: [
          { type: "value_error", loc: ["body", "email"], msg: "bad email" },
          { type: "string_too_short", loc: ["body", "password"], ctx: { min_length: 8 } },
        ],
      }),
    );
    const err = await api.register("x@y", "short", "T").catch((e: unknown) => e);
    expect((err as ApiError).message).toBe(
      "Please enter a valid email address, e.g. name@example.com.",
    );
  });
});

describe("the app's own errors still pass through unchanged", () => {
  it("detail-array with code/message (auth refusals) is shown as-is", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(401, {
        detail: [
          { code: "invalid_credentials", message: "email or password incorrect" },
        ],
      }),
    );
    const err = await api.login("a@b.dev", "password-1").catch((e: unknown) => e);
    const apiErr = err as ApiError;
    expect(apiErr.code).toBe("invalid_credentials");
    expect(apiErr.message).toBe("email or password incorrect");
  });

  it("409 email_taken still surfaces its human message", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(409, {
        detail: [{ code: "email_taken", message: "email already registered" }],
      }),
    );
    const err = await api
      .register("a@b.dev", "password-1", "T")
      .catch((e: unknown) => e);
    expect((err as ApiError).message).toBe("email already registered");
  });
});
