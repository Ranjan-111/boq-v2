import { defineConfig } from "@playwright/test";

/**
 * Browser E2E (Round 4): the gated DXF -> wall -> BOQ -> CSV journey against
 * a REAL backend (api :8099 + frontend :5173 dev servers) with real Postgres.
 *
 * Not part of `npm run test` (vitest) — run explicitly:
 *   npm run e2e          (expects `make api` + `make worker` running)
 * CI runs it in the e2e job with its own dockerized services (T126 slice).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  reporter: [["list"]],
});
