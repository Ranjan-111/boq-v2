import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, formatMoney, type Boq } from "../lib/apiClient";
import { boqStatusBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";
import { useRunStore } from "../stores/runs";
import { useExportStore } from "../stores/exports";

/** Build-from-run card when the project has no BOQ yet. */
function BuildBoqCard({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const runs = useRunStore((s) => s.runs);
  const completed = runs.filter(
    (r) => r.status === "completed" || r.status === "completed_with_exceptions",
  );
  const [runId, setRunId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.createBoq(projectId, { from_run_id: runId }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["boqs", projectId] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not build BOQ."),
  });

  return (
    <div className="card p-5">
      <h2 className="mb-1 text-sm font-semibold text-ink-900">Build BOQ from a run</h2>
      <p className="mb-3 text-xs text-ink-500">
        Only MEASURED, evidenced quantities enter a BOQ. Runs from this session
        only — the runs list endpoint arrives later.
      </p>
      {completed.length === 0 ? (
        <p className="text-xs text-ink-500">
          No completed runs this session yet — run a measurement first (Runs tab).
        </p>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor="boq-run">
              Completed run
            </label>
            <select
              id="boq-run"
              className="input !w-56 !py-1.5 !text-xs"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            >
              <option value="">Select a run…</option>
              {completed.map((r) => (
                <option key={r.id} value={r.id}>
                  Run {r.id.slice(0, 8)} ({r.stats?.measured ?? 0} measured)
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 !text-xs"
            disabled={runId === "" || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Building…" : "Build BOQ"}
          </button>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** Money + markup cells for one BOQ item row. */
function BoqItemRow({ item, currency }: { item: Boq["sections"][number]["items"][number]; currency: string }) {
  return (
    <tr className="border-b border-ink-100">
      <td className="py-2 pr-3 font-mono text-[10px] text-ink-500">{item.code}</td>
      <td className="py-2 pr-3 text-ink-800">
        {item.description}
        <span
          className="ml-2 rounded bg-ink-100 px-1 py-0.5 text-[10px] text-ink-500"
          title="Where this quantity came from"
        >
          {item.origin}
        </span>
      </td>
      <td className="py-2 pr-3 text-ink-500">{item.unit}</td>
      <td className="py-2 pr-3 text-ink-900">
        {item.quantity}
      </td>
      <td className="py-2 pr-3 text-ink-700">
        {formatMoney(item.rate_minor)} {currency}
      </td>
      <td className="py-2 pr-3 text-ink-500">{item.markup_bp} bp</td>
      <td className="py-2 pr-3 font-medium text-ink-900">
        {formatMoney(item.total_minor)} {currency}
      </td>
      <td className="py-2 pr-3 text-ink-500">{item.measurement_ids.length}</td>
    </tr>
  );
}

/** Approval actions with note + blockers surface. */
function BoqActions({ boq, projectId }: { boq: Boq; projectId: string }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<string[] | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["boqs", projectId] });
    qc.invalidateQueries({ queryKey: ["boq", boq.id] });
  };

  const submit = useMutation({
    mutationFn: () => api.submitBoq(boq.id),
    onSuccess: () => {
      setError(null);
      setBlockers(null);
      invalidate();
    },
    onError: (err) => fail(err),
  });
  const review = useMutation({
    // IN_REVIEW -> REVIEWED: the reviewer's completion hop. Approval is
    // refused server-side until this happens.
    mutationFn: () => api.reviewBoq(boq.id),
    onSuccess: () => {
      setError(null);
      setBlockers(null);
      invalidate();
    },
    onError: (err) => fail(err),
  });
  const approve = useMutation({
    mutationFn: () => api.approveBoq(boq.id, note),
    onSuccess: () => {
      setError(null);
      setBlockers(null);
      invalidate();
    },
    onError: (err) => fail(err),
  });
  const reject = useMutation({
    mutationFn: () => api.rejectBoq(boq.id, note),
    onSuccess: () => {
      setError(null);
      setBlockers(null);
      invalidate();
    },
    onError: (err) => fail(err),
  });

  function fail(err: unknown) {
    if (err instanceof ApiError) {
      setError(err.message);
      setBlockers(err.blockers);
    } else {
      setError("Action failed.");
      setBlockers(null);
    }
  }

  return (
    <div className="border-t border-ink-200 pt-3">
      <div className="flex flex-wrap items-end gap-3">
        {boq.status === "draft" ? (
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 !text-xs"
            disabled={submit.isPending}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? "Submitting…" : "Submit for review"}
          </button>
        ) : null}
        {boq.status === "in_review" || boq.status === "reviewed" ? (
          <>
            <div className="grow">
              <label className="label !mb-0.5 !text-[11px]" htmlFor="boq-note">
                Note (required for reject)
              </label>
              <input
                id="boq-note"
                className="input !py-1.5 !text-xs"
                placeholder="Approver note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
            {boq.status === "in_review" ? (
              <button
                type="button"
                className="btn-secondary !px-3 !py-1.5 !text-xs"
                disabled={review.isPending}
                onClick={() => review.mutate()}
              >
                {review.isPending ? "Marking reviewed…" : "Complete review"}
              </button>
            ) : null}
            <button
              type="button"
              className="btn-primary !px-3 !py-1.5 !text-xs"
              disabled={approve.isPending}
              onClick={() => approve.mutate()}
            >
              {approve.isPending ? "Approving…" : "Approve"}
            </button>
            <button
              type="button"
              className="btn-secondary !px-3 !py-1.5 !text-xs"
              disabled={reject.isPending || note.trim() === ""}
              onClick={() => reject.mutate()}
            >
              {reject.isPending ? "Rejecting…" : "Reject"}
            </button>
          </>
        ) : null}
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
      {blockers && blockers.length > 0 ? (
        <ul className="mt-2 space-y-1 rounded-md bg-red-50 p-2 text-xs text-red-700">
          <li className="font-medium">Approval blocked — resolve these first:</li>
          {blockers.map((b, i) => (
            <li key={i}>• {b}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Export card for an approved BOQ (polls the export job). */
function BoqExportCard({ boqId }: { boqId: string }) {
  // Action selectors are stable identities — never the whole store object,
  // which changes on every update and loops any effect that depends on it.
  const addExport = useExportStore((s) => s.addExport);
  const updateExport = useExportStore((s) => s.updateExport);
  const [error, setError] = useState<string | null>(null);
  const [activeExportId, setActiveExportId] = useState<string | null>(null);

  const createExport = useMutation({
    mutationFn: () => api.createExport(boqId, "csv"),
    onSuccess: (res) => {
      setError(null);
      setActiveExportId(res.export_id);
      addExport({
        id: res.export_id,
        boq_id: boqId,
        status: "pending",
        sha256: null,
        download_url: null,
        created_at: new Date().toISOString(),
      });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not start export."),
  });

  // Poll the active export (created here or pre-existing from this session).
  const activeExport = useQuery({
    queryKey: ["export", activeExportId],
    queryFn: () => api.getExport(activeExportId!),
    enabled: activeExportId !== null,
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 1500 : false,
  });

  // Mirror the polled export into the session store — only when a value
  // actually changed (a new-objects-every-time patch would loop zustand
  // subscribers into an infinite re-render).
  const rec = activeExport.data;
  const recStatus = rec?.status;
  const recSha = rec?.sha256;
  const recUrl = rec?.download_url;
  useEffect(() => {
    if (rec && (recStatus || recSha || recUrl)) {
      updateExport(rec.id, {
        status: recStatus,
        sha256: recSha,
        download_url: recUrl,
      });
    }
  }, [rec?.id, recStatus, recSha, recUrl, updateExport]);

  return (
    <div className="border-t border-ink-200 pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 !text-xs"
          disabled={createExport.isPending}
          onClick={() => createExport.mutate()}
        >
          {createExport.isPending ? "Starting…" : "Export CSV"}
        </button>
        {rec ? (
          <span className="text-xs">
            {rec.status === "pending" ? (
              <span className="text-blue-700">Exporting…</span>
            ) : rec.status === "failed" ? (
              <span className="text-red-700">Export failed</span>
            ) : (
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-emerald-700">Export ready</span>
                <span className="font-mono text-[10px] text-ink-400">
                  sha256 {rec.sha256?.slice(0, 16)}…
                </span>
                {rec.download_url ? (
                  <a
                    className="font-medium text-accent-700 hover:underline"
                    href={rec.download_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Download
                  </a>
                ) : null}
              </span>
            )}
          </span>
        ) : null}
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

export default function BoqTab({ projectId }: { projectId: string }) {
  const boqs = useQuery({
    queryKey: ["boqs", projectId],
    queryFn: () => api.listBoqs(projectId),
  });
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const [selectedBoqId, setSelectedBoqId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedBoqId === null && boqs.data && boqs.data.items.length > 0) {
      setSelectedBoqId(boqs.data.items[boqs.data.items.length - 1].id);
    }
  }, [boqs.data, selectedBoqId]);

  const boq = useQuery({
    queryKey: ["boq", selectedBoqId],
    queryFn: () => api.getBoq(selectedBoqId!),
    enabled: selectedBoqId !== null,
  });

  const currency = project.data?.currency ?? "";

  return (
    <div className="space-y-4">
      {boqs.isPending ? (
        <div className="card h-24 animate-pulse bg-ink-100" />
      ) : boqs.isError ? (
        <div className="card p-6 text-sm text-red-700">
          {boqs.error instanceof ApiError ? boqs.error.message : "Could not load BOQs."}
        </div>
      ) : boqs.data && boqs.data.items.length > 0 ? (
        <div className="card p-5">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-sm font-semibold text-ink-900">
              BOQ v{(boq.data?.version ?? boqs.data.items.at(-1)?.version) ?? "—"}
            </h2>
            {boq.data ? <StatusBadge badge={boqStatusBadge(boq.data.status)} /> : null}
            {boqs.data.items.length > 1 ? (
              <select
                className="input !w-40 !py-1.5 !text-xs"
                value={selectedBoqId ?? ""}
                onChange={(e) => setSelectedBoqId(e.target.value || null)}
              >
                {boqs.data.items.map((b) => (
                  <option key={b.id} value={b.id}>
                    v{b.version} ({b.status})
                  </option>
                ))}
              </select>
            ) : null}
          </div>

          {boq.isPending ? (
            <div className="h-32 animate-pulse rounded bg-ink-100" />
          ) : boq.isError ? (
            <p className="text-xs text-red-700">
              {boq.error instanceof ApiError ? boq.error.message : "Could not load the BOQ."}
            </p>
          ) : boq.data ? (
            <>
              {boq.data.sections.length === 0 ? (
                <p className="text-xs text-ink-500">
                  No sections — the run produced no mapped catalogue items.
                </p>
              ) : (
                <div className="space-y-5">
                  {boq.data.sections.map((sec) => (
                    <div key={sec.id}>
                      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
                        {sec.code} — {sec.title}
                      </h3>
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-ink-200 text-[11px] uppercase text-ink-400">
                              <th className="py-2 pr-3 font-medium">Code</th>
                              <th className="py-2 pr-3 font-medium">Description</th>
                              <th className="py-2 pr-3 font-medium">Unit</th>
                              <th className="py-2 pr-3 font-medium">Qty</th>
                              <th className="py-2 pr-3 font-medium">Rate</th>
                              <th className="py-2 pr-3 font-medium">Markup</th>
                              <th className="py-2 pr-3 font-medium">Total</th>
                              <th className="py-2 pr-3 font-medium">Meas.</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sec.items.map((item) => (
                              <BoqItemRow key={item.id} item={item} currency={currency} />
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                  <p className="text-right text-sm font-semibold text-ink-900">
                    Grand total: {formatMoney(boq.data.totals.grand_total_minor)} {currency}
                  </p>
                </div>
              )}

              <div className="mt-4">
                <BoqActions boq={boq.data} projectId={projectId} />
                {boq.data.status === "approved" || boq.data.status === "exported" ? (
                  <BoqExportCard boqId={boq.data.id} />
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      ) : (
        <BuildBoqCard projectId={projectId} />
      )}
    </div>
  );
}
