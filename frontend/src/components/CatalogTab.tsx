import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  formatMoney,
  type CatalogItem,
  type CatalogRateRow,
} from "../lib/apiClient";
import { CATALOG_UNITS, majorToMinor } from "../lib/reviewHelpers";

/** "Set default rate" inline form for one catalogue row. The API speaks
 * integer minor units; the human types a major-unit decimal (e.g. 850.00)
 * and the helper converts — never float-truncated. */
function SetRateForm({
  item,
  currency,
  onSaved,
}: {
  item: CatalogItem;
  currency: string;
  onSaved: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const minor = majorToMinor(amount);

  const save = async () => {
    if (minor === null) return;
    setPending(true);
    setError(null);
    try {
      await api.putCatalogRate(item.id, "default", {
        amount_minor: minor,
        currency,
      });
      setAmount("");
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not set the rate.");
    } finally {
      setPending(false);
    }
  };

  const fieldId = `rate-${item.id.slice(0, 8)}`;
  return (
    <div className="mt-1 flex flex-wrap items-end gap-2">
      <div>
        <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-amount`}>
          Default rate ({currency})
        </label>
        <input
          id={`${fieldId}-amount`}
          className="input !w-32 !py-1.5 !text-xs"
          inputMode="decimal"
          placeholder="e.g. 850.00"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </div>
      <button
        type="button"
        className="btn-secondary !px-3 !py-1.5 !text-xs"
        disabled={minor === null || pending}
        onClick={() => void save()}
      >
        {pending ? "Saving…" : "Set default rate"}
      </button>
      {error ? <p className="w-full text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** One catalogue row: identity + its default rate (fetched per row) + the
 * inline set-rate form. */
function CatalogRow({
  item,
  currency,
  onRateSaved,
}: {
  item: CatalogItem;
  currency: string;
  onRateSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rates = useQuery({
    queryKey: ["catalog", "rates", item.id],
    queryFn: () => api.listCatalogRates(item.id),
  });
  const defaultRate: CatalogRateRow | undefined = (rates.data?.items ?? []).find(
    (r) => r.scope === "default",
  );
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[10px] text-ink-500">{item.code}</span>
        <span className="text-xs text-ink-800">{item.description}</span>
        <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[11px] text-ink-600">
          {item.unit}
        </span>
        {defaultRate ? (
          <span className="text-xs text-ink-600">
            {formatMoney(defaultRate.amount_minor)} {defaultRate.currency} /
            {item.unit} (default)
          </span>
        ) : (
          <span className="text-[11px] text-amber-700">no default rate</span>
        )}
        <button
          type="button"
          className="text-xs font-medium text-accent-700 hover:underline"
          onClick={() => setOpen(!open)}
        >
          {open ? "Close" : "Set default rate…"}
        </button>
      </div>
      {open ? (
        <SetRateForm item={item} currency={currency} onSaved={onRateSaved} />
      ) : null}
    </li>
  );
}

/** "New item" form: code, description, unit (the known vocabulary), region
 * fixed to the project's region (catalogue items are region-scoped). */
function NewItemForm({
  regionCode,
  onCreated,
}: {
  regionCode: string;
  onCreated: () => void;
}) {
  const [code, setCode] = useState("");
  const [description, setDescription] = useState("");
  const [unit, setUnit] = useState("m");
  const [category, setCategory] = useState("walls/brick");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api.createCatalogItem({
        region_code: regionCode,
        code: code.trim(),
        description: description.trim(),
        unit,
        category_path: category.trim(),
      }),
    onSuccess: () => {
      setError(null);
      setCode("");
      setDescription("");
      onCreated();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not create the item."),
  });
  const valid =
    code.trim() !== "" && description.trim() !== "" && category.trim() !== "";
  return (
    <div className="card p-5">
      <h3 className="mb-1 text-sm font-semibold text-ink-900">New catalogue item</h3>
      <p className="mb-3 text-xs text-ink-500">
        Catalogue items are region-scoped — this one is created in region{" "}
        <span className="rounded bg-ink-100 px-1 py-0.5 text-[11px]">
          {regionCode}
        </span>
        .
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor="catalog-new-code">
            Code
          </label>
          <input
            id="catalog-new-code"
            className="input !w-28 !py-1.5 !text-xs"
            placeholder="e.g. 2.1.5"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <div className="grow">
          <label className="label !mb-0.5 !text-[11px]" htmlFor="catalog-new-desc">
            Description
          </label>
          <input
            id="catalog-new-desc"
            className="input !w-56 !py-1.5 !text-xs"
            placeholder="e.g. Brick wall 115mm thick"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor="catalog-new-unit">
            Unit
          </label>
          <select
            id="catalog-new-unit"
            className="input !w-24 !py-1.5 !text-xs"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          >
            {CATALOG_UNITS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor="catalog-new-cat">
            Category
          </label>
          <input
            id="catalog-new-cat"
            className="input !w-36 !py-1.5 !text-xs"
            placeholder="e.g. walls/brick"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 !text-xs"
          disabled={!valid || create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Creating…" : "Create item"}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/**
 * Catalogue tab — the region's items, found through search (the backend
 * has no list-all endpoint; the lexical search over the region's full set
 * IS the listing surface), plus the new-item form and the set-default-rate
 * surface.
 */
export default function CatalogTab({
  regionCode,
  currency,
}: {
  regionCode: string;
  currency: string;
}) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const search = useQuery({
    queryKey: ["catalog", "search", query, regionCode],
    queryFn: () => api.searchCatalog(query, regionCode),
    enabled: query.trim().length >= 2,
  });

  const items = search.data?.items ?? [];
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["catalog"] });
  };

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink-900">Catalogue</h2>
        <p className="mb-3 text-xs text-ink-500">
          The region's items and their default rates. Rates are integer minor
          units priced through the money kernel — the form takes a major-unit
          decimal and converts.
        </p>
        <div className="mb-3">
          <label className="label !mb-0.5 !text-[11px]" htmlFor="catalog-search">
            Search
          </label>
          <input
            id="catalog-search"
            className="input !w-72 !py-1.5 !text-xs"
            placeholder="Code or description (≥2 chars)…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {query.trim().length < 2 ? (
          <p className="text-xs text-ink-500">
            Type at least two characters to search the region's catalogue.
          </p>
        ) : search.isPending ? (
          <div className="h-8 w-64 animate-pulse rounded bg-ink-100" />
        ) : search.isError ? (
          <p className="text-xs text-red-700">
            {search.error instanceof ApiError
              ? search.error.message
              : "Search failed."}
          </p>
        ) : items.length === 0 ? (
          <p className="text-xs text-ink-500">
            No matches — try another term or create the item below.
          </p>
        ) : (
          <ul className="divide-y divide-ink-100">
            {items.map((item) => (
              <CatalogRow
                key={item.id}
                item={item}
                currency={currency}
                onRateSaved={invalidate}
              />
            ))}
          </ul>
        )}
      </div>
      <NewItemForm regionCode={regionCode} onCreated={invalidate} />
    </div>
  );
}
