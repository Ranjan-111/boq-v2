import { describe, expect, it, vi, beforeEach } from "vitest";

/**
 * Auth entry-point regressions (the manual-testing bug): the login page must
 * offer account creation, /register must be a real route, and the root must
 * land somewhere honest instead of the 404 catch-all.
 *
 * Rendered through MemoryRouter — the same <Routes> tree main.tsx registers
 * (paths + components), driven headlessly. tests/setup.ts provides the
 * localStorage baseline the auth store needs at import time.
 */
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { createRoot } from "react-dom/client";
import { act } from "react";
import LoginPage from "../src/pages/LoginPage";
import RegisterPage from "../src/pages/RegisterPage";
import RootRedirectPage from "../src/pages/RootRedirectPage";
import NotFoundPage from "../src/pages/NotFoundPage";
import AuthGuard from "../src/components/AuthGuard";
import { useAuth } from "../src/stores/auth";

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

function setLoggedIn(
  user: { id: string; email: string; display_name: string; role: string } = {
    id: "u1",
    email: "e@x.dev",
    display_name: "E",
    role: "owner",
  },
) {
  useAuth.setState({ token: "tok-1", user });
}
function setLoggedOut() {
  useAuth.setState({ token: null, user: null });
}

beforeEach(() => {
  storage.clear();
  useAuth.setState({ token: null, user: null });
});

/** The exact route table main.tsx registers (paths + components). */
function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirectPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/projects"
        element={<AuthGuard>Projects</AuthGuard>}
      />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

/** Render the route table at one URL and return the resulting HTML. */
async function renderAt(url: string): Promise<string> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <AppRoutes />
      </MemoryRouter>,
    );
  });
  const out = host.innerHTML;
  root.unmount();
  host.remove();
  return out;
}

describe("auth entry points", () => {
  it("login page links to registration", async () => {
    const html = await renderAt("/login");
    expect(html).toContain("Sign in");
    expect(html).toContain("Create account");
    expect(html).toMatch(/href="\/register"/);
  });

  it("register page renders fields and links back to sign-in", async () => {
    const html = await renderAt("/register");
    expect(html).toContain("Display name");
    expect(html).toContain("Email");
    expect(html).toContain("Password");
    expect(html).toContain("Create account");
    expect(html).toMatch(/href="\/login"/);
  });

  it("root redirects logged-out users to /login, not the 404 page", async () => {
    setLoggedOut();
    const html = await renderAt("/");
    // The redirect landed on the login page — not NotFoundPage.
    expect(html).toContain("Sign in to boq-v2");
    expect(html).not.toContain("Page not found");
  });

  it("root sends logged-in users to their projects", async () => {
    setLoggedIn();
    const html = await renderAt("/");
    expect(html).toContain("Projects");
    expect(html).not.toContain("Page not found");
  });

  it("/register is a real route: no 404 for a fresh visitor", async () => {
    setLoggedOut();
    const html = await renderAt("/register");
    expect(html).not.toContain("Page not found");
  });
});
