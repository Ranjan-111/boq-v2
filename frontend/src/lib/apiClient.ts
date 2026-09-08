/**
 * Typed API client — fetch wrapper with JWT + unified error parsing.
 *
 * Errors normalize BOTH shapes the backend can emit:
 *  - FastAPI validation/auth: {detail: [{code, message}]} or {detail: string}
 *  - RFC 7807 problem+json:   {type, title, status, code, detail?}
 * into one `ApiError`.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;

  constructor(status: number, code: string, title: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.title = title;
  }
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

function parseError(status: number, body: unknown): ApiError {
  if (typeof body === "object" && body !== null) {
    const b = body as Record<string, unknown>;
    // FastAPI detail array: [{code, message}]
    if (Array.isArray(b.detail) && b.detail.length > 0) {
      const first = b.detail[0] as Record<string, unknown>;
      return new ApiError(
        status,
        typeof first.code === "string" ? first.code : "error",
        statusText(status),
        typeof first.message === "string" ? first.message : JSON.stringify(first),
      );
    }
    // RFC 7807 problem+json (has a machine-readable `code`) — checked before
    // the plain-string detail branch because problem+json also carries a
    // human-readable detail string.
    if (typeof b.code === "string") {
      return new ApiError(
        status,
        b.code,
        typeof b.title === "string" ? b.title : statusText(status),
        typeof b.detail === "string" ? b.detail : (b.title as string) ?? "Request failed",
      );
    }
    // FastAPI plain string detail
    if (typeof b.detail === "string") {
      return new ApiError(status, "error", statusText(status), b.detail);
    }
  }
  return new ApiError(status, "error", statusText(status), "Request failed");
}

function statusText(status: number): string {
  switch (status) {
    case 400:
      return "Bad request";
    case 401:
      return "Not signed in";
    case 403:
      return "Not allowed";
    case 404:
      return "Not found";
    case 409:
      return "Conflict";
    case 422:
      return "Validation failed";
    case 429:
      return "Too many requests";
    case 503:
      return "Service unavailable";
    default:
      return `Request failed (${status})`;
  }
}

function authHeader(): Record<string, string> {
  const token = localStorage.getItem("boq.token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers: {
        ...authHeader(),
        ...(options.body !== undefined
          ? { "Content-Type": "application/json" }
          : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Network error", "Could not reach the server.");
  }

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) throw parseError(response.status, body);
  return body as T;
}

// ---------------------------------------------------------------------------
// Typed API surface (docs/api-contract.md)
// ---------------------------------------------------------------------------

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}
export interface Project {
  id: string;
  name: string;
  client_name: string | null;
  region_code: string;
  currency: string;
}
export interface ProjectList {
  items: Project[];
  next_cursor: string | null;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", { method: "POST", body: { email, password } }),
  register: (email: string, password: string, display_name: string) =>
    request<TokenResponse>("/auth/register", {
      method: "POST",
      body: { email, password, display_name },
    }),
  me: () => request<AuthUser>("/auth/me"),
  listProjects: () => request<ProjectList>("/projects"),
  createProject: (body: {
    name: string;
    client_name?: string | null;
    region_code: string;
    currency: string;
  }) => request<Project>("/projects", { method: "POST", body }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
};
