import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/apiClient";
import AppHeader from "../components/AppHeader";

/** Region display names — keyed by the catalogue's region_code. Only
 * regions with catalogue data appear in the dropdown; the label is the
 * human-facing name, the VALUE stays the code the API expects. */
const REGION_LABELS: Record<string, string> = {
  IN: "India (IN)",
};

function regionLabel(code: string): string {
  return REGION_LABELS[code] ?? code;
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [clientName, setClientName] = useState("");
  const [region, setRegion] = useState("");
  const [currency, setCurrency] = useState("INR");
  const [error, setError] = useState<string | null>(null);

  // Supported regions come from the catalogue itself — a region is offered
  // exactly when it has catalogue items to bill against (GET /catalog/regions).
  const regions = useQuery({
    queryKey: ["catalog", "regions"],
    queryFn: api.listRegions,
    staleTime: 60_000,
  });

  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        name,
        client_name: clientName || null,
        region_code: region,
        currency,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onClose();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not create project"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <div
      className="fixed inset-0 z-10 grid place-items-center bg-ink-900/40 p-4"
      onClick={onClose}
    >
      <div className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 text-base font-semibold text-ink-900">New project</h2>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label" htmlFor="p-name">Project name</label>
            <input id="p-name" className="input" value={name}
              onChange={(e) => setName(e.target.value)} required autoFocus />
          </div>
          <div>
            <label className="label" htmlFor="p-client">Client (optional)</label>
            <input id="p-client" className="input" value={clientName}
              onChange={(e) => setClientName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label" htmlFor="p-region">Region</label>
              {regions.isPending ? (
                <div className="input animate-pulse !bg-ink-100" aria-label="Loading regions" />
              ) : regions.isError ? (
                <div>
                  <p className="text-xs text-red-700">
                    Could not load supported regions.
                  </p>
                  <button
                    type="button"
                    className="btn-secondary mt-1 !px-2 !py-1 !text-xs"
                    onClick={() => regions.refetch()}
                  >
                    Retry
                  </button>
                </div>
              ) : (
                <select
                  id="p-region"
                  className="input"
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  required
                >
                  <option value="" disabled>
                    Select a region…
                  </option>
                  {regions.data?.regions.map((r) => (
                    <option key={r.region_code} value={r.region_code}>
                      {regionLabel(r.region_code)}
                      {r.item_count > 0 ? ` — ${r.item_count} catalogue items` : ""}
                    </option>
                  ))}
                </select>
              )}
              <p className="mt-1 text-[11px] text-ink-500">
                The region decides which catalogue items and rates are available
                for this project.
              </p>
            </div>
            <div>
              <label className="label" htmlFor="p-cur">Currency</label>
              <input id="p-cur" className="input" value={currency}
                onChange={(e) => setCurrency(e.target.value)} required />
            </div>
          </div>
          {error ? (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          ) : null}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={create.isPending || !name || !region}>
              {create.isPending ? "Creating…" : "Create project"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function ProjectsPage() {
  const [showModal, setShowModal] = useState(false);
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

  return (
    <div className="min-h-full">
      <AppHeader />
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-xl font-semibold text-ink-900">Projects</h1>
          <button className="btn-primary" onClick={() => setShowModal(true)}>
            New project
          </button>
        </div>

        {projects.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="card h-24 animate-pulse bg-ink-100" />
            ))}
          </div>
        ) : projects.isError ? (
          <div className="card p-8 text-center">
            <p className="mb-2 text-sm font-medium text-red-700">
              {projects.error instanceof ApiError
                ? projects.error.message
                : "Could not load projects."}
            </p>
            <button
              className="btn-secondary"
              onClick={() => projects.refetch()}
            >
              Retry
            </button>
          </div>
        ) : projects.data && projects.data.items.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.data.items.map((p) => (
              <a key={p.id} href={`/projects/${p.id}`} className="card p-5 transition-shadow hover:shadow-md">
                <h2 className="mb-1 truncate text-sm font-semibold text-ink-900">{p.name}</h2>
                <p className="text-xs text-ink-500">{p.client_name ?? "—"}</p>
                <p className="mt-3 flex gap-2 text-[11px] text-ink-400">
                  <span className="rounded bg-ink-100 px-1.5 py-0.5">{p.region_code}</span>
                  <span className="rounded bg-ink-100 px-1.5 py-0.5">{p.currency}</span>
                </p>
              </a>
            ))}
          </div>
        ) : (
          <div className="card grid place-items-center p-12 text-center">
            <p className="text-sm font-medium text-ink-700">No projects yet</p>
            <p className="mt-1 text-sm text-ink-500">
              Create your first project to start uploading drawings.
            </p>
          </div>
        )}
      </main>
      {showModal ? <NewProjectModal onClose={() => setShowModal(false)} /> : null}
    </div>
  );
}
