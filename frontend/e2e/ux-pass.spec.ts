import { test, expect } from "@playwright/test";
import { seedInRegionCatalog } from "./support/seedCatalog";

/**
 * Manual-testing regression spec (post-R9 UX pass):
 *   1. invalid email on /register → the humanized message, NEVER raw
 *      Pydantic JSON ({"type":"value_error",...} stayed in the UI before).
 *   2. New Project region is a dropdown of SUPPORTED regions (from
 *      GET /catalog/regions — live catalogue data, only regions with items),
 *      and the selected region is what the API receives.
 *   3. Upload staging is visible: the honest indeterminate pipeline with
 *      stages from real job state (asserted in the DXF journey too — here
 *      we pin the strip exists while a job is queued/running).
 *
 * Requires the standard local stack (api :8099 + worker + vite :5173).
 */
test("register validation + region dropdown + upload staging", async ({ page }) => {
  // --- 1. Invalid email: human sentence, never raw JSON -----------------
  await page.goto("/register");
  await page.getByLabel("Display name", { exact: true }).fill("UX Pass");
  await page.getByLabel("Email", { exact: true }).fill("fkuy@ghf");
  await page.getByLabel("Password", { exact: true }).fill("password-for-ux-1");
  await page.getByRole("button", { name: "Create account" }).click();

  const errBox = page.getByText(/valid email address/i);
  await expect(errBox).toBeVisible({ timeout: 10_000 });
  // The raw Pydantic structure must not appear anywhere on the page.
  await expect(page.getByText(/"type"/)).toHaveCount(0);
  await expect(page.getByText(/value_error/)).toHaveCount(0);
  await expect(page.getByText(/"loc"/)).toHaveCount(0);
  await expect(page.getByText(/value is not a valid email address/)).toHaveCount(0);

  // --- Fix the email and register for real ------------------------------
  const email = `uxpass-${Date.now()}@example.com`;
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("button", { name: "New project" })).toBeVisible();

  // --- 2. Region dropdown from supported regions ------------------------
  // Seed the catalogue-backed IN region first — on a fresh database the
  // dropdown is empty until an item exists (CI runs against one).
  await seedInRegionCatalog(page);
  await page.getByRole("button", { name: "New project" }).click();
  const regionSelect = page.getByLabel("Region");
  // The dropdown offers the seeded IN region (the only catalogue-backed
  // region in this environment), with the item count made explicit.
  await expect(regionSelect).toBeVisible();
  // <option> is hidden in the a11y tree — assert through the combobox's
  // text (its selected placeholder) then select by value.
  await expect(regionSelect).toHaveText(/select a region/i, { timeout: 10_000 });
  await regionSelect.selectOption("IN");
  await expect(regionSelect).toHaveText(/india/i);

  await page.getByLabel("Project name").fill("UX Pass Project");
  await page.getByLabel("Currency").fill("INR");
  await page.getByRole("button", { name: "Create project" }).click();
  // The modal closes and the new card appears on the projects list — open
  // its workspace (the upload surface lives there).
  await page.getByRole("link", { name: /UX Pass Project/ }).click();
  await expect(
    page.getByRole("heading", { name: "UX Pass Project" }),
  ).toBeVisible({ timeout: 10_000 });

  // The created project's region came from the dropdown selection (the
  // workspace shows the region chip "IN").
  await expect(page.getByText("IN", { exact: true }).first()).toBeVisible();

  // --- 3. Upload staging: the honest pipeline strip ---------------------
  const fileInput = page.getByLabel("Upload drawing file");
  await fileInput.setInputFiles("../tests/fixtures/dxf/wall_plan.dxf");
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  // The staged pipeline is visible with the honest stages. The job
  // completes fast locally, so assert the strip OR the completed state —
  // both prove the staging UI replaced the opaque "polling the job" text.
  await expect(
    page.getByLabel("Upload progress"),
  ).toBeVisible({ timeout: 15_000 });
  // The old opaque label must never appear.
  await expect(page.getByText(/polling the job/i)).toHaveCount(0);

  // Terminal state: parsing completes and the drawing list shows parsed.
  await expect(page.getByText("parsed").first()).toBeVisible({ timeout: 30_000 });
});

/**
 * Parse failure handling: a structurally broken DXF (SECTION+ENTITIES
 * markers so the magic-byte sniff accepts it, but garbage ezdxf cannot
 * parse) surfaces the HUMAN failure card with a retry — never a silent
 * hang. The failure arrives inside a SUCCEEDED job ({ok:false} result),
 * which is exactly the path the old UI missed.
 */
test("parse failure shows human error + retry", async ({ page }) => {
  await page.goto("/projects");
  const email = `uxfail-${Date.now()}@example.com`;
  await page.evaluate(
    async (payload) => {
      const res = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`register failed: ${res.status}`);
      const body = (await res.json()) as { access_token: string };
      localStorage.setItem("boq.token", body.access_token);
    },
    { email, password: "uxfail-password-1", display_name: "UX Fail" },
  );
  // Seed before the form — the region dropdown is catalogue-backed.
  await seedInRegionCatalog(page);
  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).click();
  await page
    .locator("select#p-region")
    .waitFor({ state: "visible", timeout: 15_000 });
  await page.getByLabel("Region").selectOption("IN");
  await page.getByLabel("Project name").fill("UX Fail Project");
  await page.getByLabel("Currency").fill("INR");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: /UX Fail Project/ }).click();
  await expect(
    page.getByRole("heading", { name: "UX Fail Project" }),
  ).toBeVisible({ timeout: 10_000 });

  // A structurally broken DXF — accepted by upload, refused by the parser.
  const badPath = test.info().outputPath("broken.dxf");
  const { writeFileSync } = await import("node:fs");
  writeFileSync(badPath, "0\nSECTION\n2\nENTITIES\n0\nGARBAGE\n");
  await page.getByLabel("Upload drawing file").setInputFiles(badPath);
  await page.getByRole("button", { name: "Upload", exact: true }).click();

  // The failure card: human phrasing (softened library noise) + retry.
  await expect(
    page.getByText(/Parse failed: The DXF file could not be read/i),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /retry parse/i })).toBeVisible();
  // The internal exception name never reaches the user-facing card.
  await expect(page.getByText("ezdxf")).toHaveCount(0);
});
