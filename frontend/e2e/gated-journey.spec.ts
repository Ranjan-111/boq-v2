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

  // Round 6 (T072): the audited human correction — the ONLY way a quantity
  // may change. Correct Wall 1's LENGTH through the review UI (value +
  // mandatory reason); the original stays visible (struck through) beside
  // the corrected number, and the BOQ below bills the corrected sum.
  // Rows are addressed by LABEL, never position — the list orders by row
  // id (UUID), so position is not an identity.
  const wallRow = page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "Wall 1", exact: true }) });
  await expect(wallRow).toBeVisible({ timeout: 30_000 });
  const wallValueText = (await wallRow.getByRole("cell").nth(2).textContent()) ?? "";
  const wallValue = Number.parseFloat(wallValueText);
  expect(Number.isFinite(wallValue)).toBe(true);
  const wall2Row = page
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "Wall 2", exact: true }) });
  const wall2Value = Number.parseFloat(
    (await wall2Row.getByRole("cell").nth(2).textContent()) ?? "",
  );
  await wallRow.getByRole("button", { name: /correct…/i }).click();
  const correctedInput = wallRow.getByLabel(/corrected value/i);
  await expect(correctedInput).toBeVisible();
  await correctedInput.fill(String(wallValue + 2));
  await wallRow.getByLabel(/reason \(required, audited\)/i).fill("e2e: site tape measured 2 m more");
  await wallRow.getByRole("button", { name: /save correction/i }).click();
  await expect(page.getByText("corrected").first()).toBeVisible({ timeout: 15_000 });
  // The original engine value stays visible (struck through) beside it —
  // the "original always visible" doctrine, proven in the browser.
  await expect(
    wallRow.locator("span.line-through", { hasText: wallValue.toFixed(6) }),
  ).toBeVisible({ timeout: 15_000 });
  const expectedBoqSum = `${(wallValue + 2 + wall2Value).toFixed(6)}`;

  // BOQ: build from the completed run, submit, complete review, approve.
  await page.getByRole("button", { name: "BOQ", exact: true }).click();
  const runSelect = page.getByRole("combobox", { name: /completed run/i });
  await expect(runSelect.getByRole("option", { name: /measured\)/ })).toBeAttached();
  await runSelect.selectOption({ index: 1 }); // the (only) completed session run
  // The option VALUE is the full run id — captured for the API-side
  // exception resolution below.
  const runId = await runSelect.inputValue();
  expect(runId).toMatch(/^[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: /build boq/i }).first().click();
  await expect(page.getByText(/brick wall/i)).toBeVisible({ timeout: 30_000 });
  // The corrected length flows into the billable line: the m group bills
  // wall1(corrected) + wall2 — the correction is the quantity's provenance.
  await expect(page.getByText(expectedBoqSum).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /submit for review/i }).click();
  await expect(page.getByText(/in review/i).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /complete review/i }).click();
  await expect(page.getByText(/^reviewed$/)).toBeVisible({ timeout: 15_000 });

  // Round 5 trust closure: the run's m2 rule groups (wall footprint AND
  // wall net of openings) collide on the single m2 catalogue unit and the
  // count group has no item at all — auto-mapping cannot pick who bills,
  // so approve is refused server-side and the UI surfaces the unmapped
  // blockers. The human resolves them (their decision), then approval passes.
  await page.getByRole("button", { name: "Approve" }).click();
  const blockerMsg = page.getByText(/unmapped_measurement: measurement not mapped/i);
  await expect(blockerMsg.first()).toBeVisible({ timeout: 15_000 });
  await page.evaluate(async (fullRunId) => {
    const t = localStorage.getItem("boq.token")!;
    const res = await fetch(`/api/v1/runs/${fullRunId}/exceptions`, {
      headers: { Authorization: `Bearer ${t}` },
    });
    if (!res.ok) throw new Error(`exception list failed: ${res.status}`);
    const body = (await res.json()) as { items: { id: string; code: string }[] };
    const unresolved = body.items.filter((e) => e.code === "unmapped_measurement");
    if (unresolved.length === 0) throw new Error("no unmapped blockers found");
    for (const exc of unresolved) {
      const done = await fetch(`/api/v1/exceptions/${exc.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${t}` },
        body: JSON.stringify({ resolution: "mapped by hand (E2E journey)" }),
      });
      if (!done.ok) throw new Error(`resolve failed: ${done.status}`);
    }
  }, runId);

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/approved/i).first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /export csv/i }).click();
  await expect(page.getByText(/export ready/i)).toBeVisible({ timeout: 60_000 });
  const download = page.getByRole("link", { name: /download/i });
  await expect(download).toBeVisible();

  // Round 6 (T075): the audit trail — every decision this journey made is
  // on one screen: the correction, the approvals, the export.
  await page.getByRole("button", { name: "Audit", exact: true }).click();
  await expect(
    page.getByText("correct_quantity").first(),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("approve").first()).toBeVisible();
});
