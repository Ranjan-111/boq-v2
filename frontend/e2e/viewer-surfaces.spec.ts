import { test, expect } from "@playwright/test";
import { seedInRegionCatalog } from "./support/seedCatalog";

/**
 * Post-R9 manual pass, browser-first surfaces:
 *
 *  1. Viewer base layer — a completed run shows the DRAWING (its classified
 *     base geometry) even before any measurement row is selected; the
 *     pre-fix behavior was a blank page with "select a measurement row".
 *  2. Exception guidance — every exception row explains what it means, why
 *     it blocks, and the correct action; Resolve is offered ONLY where a
 *     recorded human decision is the resolution.
 *  3. BOQ build state-aware refusal — a run with zero measured quantities
 *     explains WHY through the run's own state, instead of the raw red
 *     backend line.
 *
 * Requires: `make api` + `make worker` (8099), Postgres, `npm run dev` (5173).
 */

interface RegisterResponse {
  access_token: string;
}

async function apiRegister(page: import("@playwright/test").Page, email: string) {
  return page.evaluate(
    async (payload) => {
      const res = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`register failed: ${res.status}`);
      const body = (await res.json()) as RegisterResponse;
      localStorage.setItem("boq.token", body.access_token);
      return body.access_token;
    },
    { email, password: "e2e-password-1", display_name: "E2E" },
  );
}

test("viewer shows base geometry + guidance-driven exceptions + state-aware BOQ refusal", async ({
  page,
}) => {
  await page.goto("/projects");
  const email = `e2e-view-${Date.now()}@example.com`;
  await apiRegister(page, email);
  // Seed before the form — the region dropdown is catalogue-backed.
  await seedInRegionCatalog(page);
  await page.goto("/projects");

  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill("E2E Viewer Surfaces");
  await page.locator("select#p-region").waitFor({ state: "visible", timeout: 15_000 });
  await page.locator("select#p-region").selectOption("IN");
  await page.getByRole("button", { name: /create project/i }).click();
  // Enter the project workspace (its tabs own upload/run/BOQ surfaces).
  await page
    .getByRole("link", { name: /E2E Viewer Surfaces/i })
    .click({ timeout: 15_000 });

  // Upload the known-good DXF through the real upload flow.
  await page.getByRole("button", { name: "Drawings", exact: true }).click();
  const fileInput = page.getByLabel("Upload drawing file");
  await fileInput.setInputFiles("../tests/fixtures/dxf/wall_plan.dxf");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText(/parsed/i).first()).toBeVisible({ timeout: 60_000 });

  // Human scale gate — inside the drawing's expandable sheet row.
  await page
    .getByRole("button", { name: /wall_plan\.dxf.*parsed/i })
    .click({ timeout: 15_000 });
  await page.getByLabel(/units per drawing unit/i).fill("1.0");
  await page.getByRole("button", { name: /confirm scale/i }).click();
  await expect(page.getByText(/scale confirmed/i).first()).toBeVisible({ timeout: 15_000 });

  // Run to completion.
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await page.getByRole("button", { name: /start run/i }).click();
  await expect(page.getByText(/measured/i).first()).toBeVisible({ timeout: 60_000 });

  // (1) VIEWER BASE LAYER — before touching any measurement row, the run's
  // classified geometry renders (SVG polygons), never the old blank hint.
  const svg = page.locator("svg").first();
  await expect(svg).toBeVisible({ timeout: 15_000 });
  await expect(svg.locator("polygon, polyline").first()).toBeVisible();
  // The layer legend explains the base/evidence distinction.
  await expect(
    page.getByText(/classified base geometry/i),
  ).toBeVisible({ timeout: 10_000 });

  // (2) EXCEPTION GUIDANCE — this clean fixture run has no exceptions; the
  // honest empty state shows. (The guidance rendering itself is pinned by
  // vitest exceptionGuidance.test.ts; here we assert the panel exists and
  // states the honest situation.)
  await expect(
    page.getByText(/no exceptions|all exceptions resolved/i).first(),
  ).toBeVisible({ timeout: 10_000 });

  // (3) STATE-AWARE BOQ — build from the completed run (it HAS measured
  // quantities): the BOQ builds normally (no refusal, no red raw line).
  await page.getByRole("button", { name: "BOQ", exact: true }).click();
  const runSelect = page.getByRole("combobox", { name: /completed run/i });
  await expect(runSelect.getByRole("option", { name: /measured\)/ })).toBeAttached();
  await runSelect.selectOption({ index: 1 });
  await page.getByRole("button", { name: /build boq/i }).first().click();
  await expect(page.getByText(/brick wall|mapped/i).first()).toBeVisible({
    timeout: 30_000,
  });
  // No raw backend refusal text anywhere on the panel.
  await expect(
    page.getByText(/run has no measured quantities/i),
  ).toHaveCount(0);
});
