import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { ApiError } from "../src/lib/apiClient";

/**
 * We test the error-parser indirectly through a real fetch round-trip:
 * mock global fetch, call api.* endpoints, assert normalized ApiError.
 * localStorage is stubbed explicitly so the test works in any environment.
 */
import { api } from "../src/lib/apiClient";

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
const localStorageStub = {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
  clear: () => void storage.clear(),
};

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", localStorageStub);
});

afterEach(() => {
  
});

describe("api error normalization", () => {
  it("parses FastAPI detail-array errors into ApiError with code", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(401, {
        detail: [{ code: "invalid_credentials", message: "email or password incorrect" }],
      }),
    );
    const err = await api.login("a@b.dev", "password-123").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(401);
    expect(apiErr.code).toBe("invalid_credentials");
    expect(apiErr.message).toBe("email or password incorrect");
    
  });

  it("parses RFC 7807 problem+json errors", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(409, {
        type: "https://boq-v2.dev/problems/blocked",
        title: "Approve first",
        status: 409,
        code: "approval_required",
        detail: "2 blockers unresolved",
      }),
    );
    const err = await api.listProjects().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe("approval_required");
    expect(apiErr.title).toBe("Approve first");
    
  });

  it("parses plain-string FastAPI detail", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "field required" }));
    const err = await api.getProject("xyz").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("field required");
    
  });

  it("turns network failures into a network_error ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    const err = await api.listProjects().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("network_error");
    
  });

  it("attaches bearer token from localStorage", async () => {
    localStorage.setItem("boq.token", "tok123");
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", spy);
    await api.listProjects();
    const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok123");
    
  });
});
