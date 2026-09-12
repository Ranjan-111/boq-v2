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

describe("expired-session redirect", () => {
  const realLocation = window.location;

  afterEach(() => {
    // jsdom allows deleting once then reassigning
    (window as unknown as { location: Location }).location = realLocation;
    vi.unstubAllGlobals();
  });

  function stubLocation(path: string) {
    const assign = vi.fn();
    const loc = {
      pathname: path,
      search: "",
      assign,
      href: `http://localhost${path}`,
    };
    // jsdom location is non-writable; replace the whole property
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: loc,
    });
    return assign;
  }

  it("clears credentials and redirects to /login?expired=1 on token-level 401", async () => {
    localStorage.setItem("boq.token", "dead-token");
    localStorage.setItem("boq.user", '{"email":"a@b.dev"}');
    vi.stubGlobal(
      "fetch",
      mockFetch(401, {
        detail: [{ code: "invalid_token", message: "malformed or expired token" }],
      }),
    );
    const assign = stubLocation("/projects/123?tab=runs");
    const err = await api.listProjects().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("invalid_token");
    // the dead session is cleared...
    expect(localStorage.getItem("boq.token")).toBeNull();
    expect(localStorage.getItem("boq.user")).toBeNull();
    // ...and the user lands on /login with the return target preserved
    expect(assign).toHaveBeenCalledWith(
      "/login?expired=1&from=%2Fprojects%2F123%3Ftab%3Druns",
    );
  });

  it("does NOT sign out on wrong-password (invalid_credentials)", async () => {
    localStorage.setItem("boq.token", "tok");
    const assign = stubLocation("/login");
    vi.stubGlobal(
      "fetch",
      mockFetch(401, {
        detail: [{ code: "invalid_credentials", message: "email or password incorrect" }],
      }),
    );
    await api.login("a@b.dev", "wrong").catch(() => undefined);
    expect(assign).not.toHaveBeenCalled();
    expect(localStorage.getItem("boq.token")).toBe("tok");
  });

  it("does NOT redirect for business refusals (409)", async () => {
    const assign = stubLocation("/projects/1");
    vi.stubGlobal(
      "fetch",
      mockFetch(409, {
        type: "b",
        title: "t",
        status: 409,
        code: "no_measured",
        detail: "run has no measured quantities",
      }),
    );
    const err = await api.createBoq("p", { from_run_id: "r" }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(assign).not.toHaveBeenCalled();
  });
});
