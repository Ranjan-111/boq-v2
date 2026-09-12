import { test, expect } from "@playwright/test";

/**
 * Focused regression for the auth entry-point fix:
 *   /            -> redirect (not the 404 catch-all)
 *   /register    -> real page (was 404 before the route existed)
 *   login page   -> links to registration
 *   register -> logout -> login -> projects round-trip through the UI only.
 */
test("root and register entry points + register -> login -> projects", async ({ page }) => {
  // Fresh visitor: / must NOT render the 404 catch-all — it redirects to /login.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in to boq-v2" })).toBeVisible();
  await expect(page.getByText("Page not found")).toHaveCount(0);

  // The login page offers account creation (the missing entry point).
  await page.getByRole("link", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/register$/);
  await expect(page.getByRole("heading", { name: /create your account/i })).toBeVisible();

  // Register through the UI (no API shortcuts — this is the regression).
  const email = `regfix-${Date.now()}@example.com`;
  await page.getByLabel("Display name", { exact: true }).fill("Reg Fix");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("regfix-password-1");
  await page.getByRole("button", { name: "Create account" }).click();

  // Registration signs the user in and lands on their projects.
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("button", { name: "New project" })).toBeVisible();

  // Sign out, then sign back in through the login form (same user, real login).
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill("regfix-password-1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("button", { name: "New project" })).toBeVisible();

  // Logged-in root now goes straight to projects (no 404 catch-all).
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByText("Page not found")).toHaveCount(0);
});
