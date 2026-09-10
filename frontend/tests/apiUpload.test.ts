import { describe, expect, it, vi, beforeEach } from "vitest";
import { api, ApiError, postForm } from "../src/lib/apiClient";

/**
 * Multipart upload + 409-blockers normalization. Fetch is stubbed globally
 * (same style as tests/apiClient.test.ts) and we assert on the exact
 * RequestInit the client hands to fetch.
 */

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

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("postForm multipart upload", () => {
  it("sends a FormData body with Authorization and NO Content-Type header", async () => {
    localStorage.setItem("boq.token", "tok-upload");
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(202, { drawing_file_id: "df_1", job_id: "job_1" }),
    );
    vi.stubGlobal("fetch", spy);

    const file = new File(["(DXF content)"], "plan.dxf", { type: "application/dxf" });
    const res = await api.uploadDrawing("proj_1", file);

    expect(res).toEqual({ drawing_file_id: "df_1", job_id: "job_1" });

    const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("POST");
    // The browser must set the multipart boundary — the client must NOT.
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-upload");
    expect(headers["Content-Type"]).toBeUndefined();
    expect(headers["content-type"]).toBeUndefined();
    // The body is the multipart FormData with the file under "file".
    expect(init?.body).toBeInstanceOf(FormData);
    const fd = init?.body as FormData;
    expect(fd.get("file")).toBe(file);
  });

  it("attaches no Authorization header when signed out", async () => {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        jsonResponse(202, { drawing_file_id: "df_2", job_id: "job_2" }),
    );
    vi.stubGlobal("fetch", spy);
    await postForm("/projects/p/drawings", new FormData());
    const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("normalizes problem+json upload errors (e.g. unsupported format)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          jsonResponse(415, {
            code: "unsupported_format",
            title: "Unsupported format",
            detail: "only dxf/pdf/png/jpg/webp are accepted",
          }),
      ),
    );
    const err = await api
      .uploadDrawing("proj_1", new File(["x"], "a.docx"))
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(415);
    expect((err as ApiError).code).toBe("unsupported_format");
    expect((err as ApiError).message).toBe("only dxf/pdf/png/jpg/webp are accepted");
  });

  it("extracts the blockers list from a 409 blockers_present problem", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          jsonResponse(409, {
            code: "blockers_present",
            title: "Approve blocked",
            detail: [
              { message: "2 unresolved blocking exceptions on run r_9" },
              "BOQ item b_3 has no measurement evidence",
            ],
          }),
      ),
    );
    const err = await api.approveBoq("boq_1", "ok").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe("blockers_present");
    expect(apiErr.blockers).toEqual([
      "2 unresolved blocking exceptions on run r_9",
      "BOQ item b_3 has no measurement evidence",
    ]);
    expect(apiErr.message).toContain("2 unresolved blocking exceptions");
  });

  it("still keeps the plain-string detail contract for problems", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          jsonResponse(409, {
            code: "approval_required",
            title: "Approve first",
            detail: "2 blockers unresolved",
          }),
      ),
    );
    const err = await api.approveBoq("boq_1", "ok").catch((e: unknown) => e);
    expect((err as ApiError).message).toBe("2 blockers unresolved");
    expect((err as ApiError).blockers).toBeNull();
  });
});
