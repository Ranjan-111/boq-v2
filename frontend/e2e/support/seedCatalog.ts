import type { Page } from "@playwright/test";

/**
 * Shared E2E seeding: the catalogue-backed "IN" region every journey needs.
 *
 * The New Project form offers regions from GET /catalog/regions — live
 * catalogue data, ONLY regions with items. A fresh database (CI's e2e job
 * creates one per run) has no items, so the dropdown would offer nothing
 * and every selectOption("IN") would hang. Seed the IN catalogue item via
 * the API BEFORE opening the New Project form; the call is idempotent
 * (409 = a previous run already seeded it — the catalogue is
 * workspace-global), and the default rate is set the same way.
 */
export async function seedInRegionCatalog(page: Page): Promise<void> {
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
    } else if (create.status === 409 || create.status === 500) {
      // 409 = a previous run seeded it. 500 = a CONCURRENT run won the
      // insert and this request lost the unique-key race (the API has not
      // turned that IntegrityError into a 409); the item exists either way
      // — resolve it by search, with a short retry while the winner's
      // transaction is still committing.
      let id_found: string | null = null;
      for (let attempt = 0; attempt < 5 && id_found === null; attempt++) {
        const res = await fetch("/api/v1/catalog/search?q=2.1.1&region_code=IN", {
          headers: { Authorization: `Bearer ${t}` },
        });
        if (!res.ok) throw new Error(`catalog lookup failed: ${res.status}`);
        const found = (await res.json()) as { items: { id: string }[] };
        id_found = found.items[0]?.id ?? null;
        if (id_found === null) await new Promise((r) => setTimeout(r, 200));
      }
      if (id_found === null) throw new Error("seeded item not found after retries");
      id = id_found;
    } else {
      throw new Error(`catalog seed failed: ${create.status}`);
    }
    const rate = await fetch(`/api/v1/catalog/items/${id}/rates/default`, {
      method: "PUT", headers: hdr,
      body: JSON.stringify({ amount_minor: 85000, currency: "INR" }),
    });
    if (rate.status !== 200) {
      // A CONCURRENT seeder may have inserted its own rate row between our
      // select and insert (the upsert's scalar_one_or_none then sees
      // multiple rows and the API answers 500). The rate exists either way
      // — accept the loss if any default rate is present.
      const check = await fetch(`/api/v1/catalog/items/${id}/rates`, {
        headers: { Authorization: `Bearer ${t}` },
      });
      if (!check.ok) throw new Error(`rate check failed: ${check.status}`);
      const rows = (await check.json()) as { items: unknown[] };
      if (rows.items.length === 0) {
        throw new Error(`rate seed failed: ${rate.status}`);
      }
    }
  });
}
