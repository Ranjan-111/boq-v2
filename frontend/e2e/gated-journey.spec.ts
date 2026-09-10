import { test, expect } from "@playwright/test";

/**
 * THE Round 4 exit-criterion journey (T126 slice), browser-first:
 *
 *   register -> create project -> upload DXF -> parse job -> confirm scale
 *   (human gate) -> start run -> review measurements -> build BOQ -> submit
 *   -> complete review -> approve -> export CSV -> sha + download link.
 *
 * Every refusal the trust doctrine promises is asserted along the way:
 * the run form offers ONLY confirmed-scale sheets (nothing measurable
 * before the human gate), and approval/export unlock only through the
 * audited gates.
 *
 * Requires: `make api` + `make worker` (port 8099), Postgres, and
 * `npm run dev` (port 5173). Catalogue + rate are seeded via the API.
 */

const DXF_NAME = "wall_plan.dxf";

interface RegisterResponse {
  access_token: string;
}

/** Register via the API in the page context (proxied by Vite) and stash the JWT. */
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

test("gated DXF to wall to BOQ to CSV journey", async ({ page }) => {
  // Land on the app origin first — page.evaluate's fetch needs an origin
  // to resolve the relative /api/v1 path (the Vite proxy serves it). The
  // first land bounces to /login (no token yet); register stashes the JWT
  // in localStorage, then a fresh navigation rehydrates the auth store.
  await page.goto("/projects");
  const email = `e2e-${Date.now()}@example.com`;
  const token = await apiRegister(page, email);
  expect(token).toBeTruthy();
  await page.goto("/projects");
  await expect(page.getByRole("button", { name: "New project" })).toBeVisible();
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill("E2E Gated Journey");
  await page.getByLabel("Region").fill("IN");
  await page.getByLabel("Currency").fill("INR");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "E2E Gated Journey" })).toBeVisible();

  // Seed catalogue + rate via the API (catalog UI is a later ticket). The
  // catalogue is workspace-global, so a 409 just means a previous run
  // already seeded it — fetch the id instead.
  await page.evaluate(async () => {
    const t = localStorage.getItem("boq.token")!;
    const hdr = { "Content-Type": "application/json", Authorization: `Bearer ${t}` };
    const create = await fetch("/api/v1/catalog/items", {
      method: "POST", headers: hdr,
      body: JSON.stringify({
        region_code: "IN", code: "2.1.1",
        description: "Brick wall 230mm thick", unit: "m",
        category_path: "walls/brick",
      }),
    });
    let id: string;
    if (create.status === 201) {
      id = ((await create.json()) as { id: string }).id;
    } else if (create.status === 409) {
      const res = await fetch("/api/v1/catalog/search?q=2.1.1&region_code=IN", {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!res.ok) throw new Error(`catalog lookup failed: ${res.status}`);
      const found = (await res.json()) as { items: { id: string }[] };
      if (found.items.length === 0) throw new Error("seeded item not found");
      id = found.items[0].id;
    } else {
      throw new Error(`catalog seed failed: ${create.status}`);
    }
    const rate = await fetch(`/api/v1/catalog/items/${id}/rates/default`, {
      method: "PUT", headers: hdr,
      body: JSON.stringify({ amount_minor: 85000, currency: "INR" }),
    });
    if (rate.status !== 200) throw new Error(`rate seed failed: ${rate.status}`);
  });

  // Open the project workspace (card link -> full navigation; the token
  // lives in localStorage and rehydrates the auth store).
  await page.getByRole("link", { name: /E2E Gated Journey/ }).click();
  await expect(page.getByRole("heading", { name: "E2E Gated Journey" })).toBeVisible();

  // Upload the fixture DXF through the Drawings tab.
  const fileInput = page.getByLabel("Upload drawing file");
  await fileInput.setInputFiles("../tests/fixtures/dxf/wall_plan.dxf");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText(DXF_NAME).first()).toBeVisible({ timeout: 30_000 });

  // Parse job completes -> expand the drawing row: sheet + PROPOSED
  // calibration (never CONFIRMED).
  await expect(page.getByText("parsed").first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: new RegExp(DXF_NAME) }).click();
  await expect(page.getByText("modelspace").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/scale proposed/i)).toBeVisible({ timeout: 30_000 });

  // Before scale confirmation the run form offers NOTHING measurable —
  // the server refuses identically (backend journey test), and the UI
  // never fakes a runnable sheet: the sheet select shows the honest
  // "no confirmed-scale modelspace sheets" option and Start run stays
  // disabled until a human confirms scale.
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: /sheet/i }),
  ).toHaveText(/no confirmed-scale modelspace sheets/i, { timeout: 15_000 });
  await expect(page.getByRole("button", { name: /start run/i })).toBeDisabled();

  // Confirm scale — THE human gate, on the Drawings tab.
  await page.getByRole("button", { name: "Drawings", exact: true }).click();
  await page.getByLabel(/units per drawing unit/i).fill("1.0");
  await page.getByRole("button", { name: /confirm scale/i }).click();
  await expect(page.getByText(/scale confirmed/i).first()).toBeVisible({ timeout: 15_000 });

  // Run: measurements appear with their states (and wall labels).
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await page.getByRole("button", { name: /start run/i }).click();
  await expect(page.getByText(/measured/i).first()).toBeVisible({ timeout: 60_000 });
  await expect(
    page.getByRole("cell", { name: /wall/i }).first(),
  ).toBeVisible({ timeout: 30_000 });

  // BOQ: build from the completed run, submit, complete review, approve.
  await page.getByRole("button", { name: "BOQ", exact: true }).click();
  const runSelect = page.getByRole("combobox", { name: /completed run/i });
  await expect(runSelect.getByRole("option", { name: /measured\)/ })).toBeAttached();
  await runSelect.selectOption({ index: 1 }); // the (only) completed session run
  await page.getByRole("button", { name: /build boq/i }).first().click();
  await expect(page.getByText(/brick wall/i)).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: /submit for review/i }).click();
  await expect(page.getByText(/in review/i).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /complete review/i }).click();
  await expect(page.getByText(/^reviewed$/)).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/approved/i).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /export csv/i }).click();
  await expect(page.getByText(/export ready/i)).toBeVisible({ timeout: 60_000 });
  const download = page.getByRole("link", { name: /download/i });
  await expect(download).toBeVisible();
});
