import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  formatMoney,
  type Boq,
  type CatalogItem,
  type ExceptionRow,
  type Measurement,
} from "../lib/apiClient";
import { boqStatusBadge, severityBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";
import { unmappedExceptionMessage } from "../lib/reviewHelpers";

/** Build-from-run card when the project has no BOQ yet. The picker offers
 * the project's FULL run history (list-runs) — completed ones selectable. */
function BuildBoqCard({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: ["runs", projectId],
    queryFn: () => api.listRuns(projectId),
  });
  const completed = (runs.data?.items ?? []).filter(
    (r) => r.status === "completed" || r.status === "completed_with_exceptions",
  );
  const [runId, setRunId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.createBoq(projectId, { from_run_id: runId }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["boqs", projectId] });
      // The build PERSISTS unmapped blockers for the run's unmapped groups —
      // any cached exceptions list for this run (e.g. the Runs tab's, fetched
      // while the run had none) is now stale. Invalidate by the list's
      // leading key so every ["exceptions", runId] entry refetches.
      qc.invalidateQueries({ queryKey: ["exceptions", runId] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Could not build BOQ."),
  });

  return (
    <div className="card p-5">
      <h2 className="mb-1 text-sm font-semibold text-ink-900">Build BOQ from a run</h2>
      <p className="mb-3 text-xs text-ink-500">
        Only MEASURED, evidenced quantities enter a BOQ. Any completed run of
        this project is eligible.
      </p>
      {runs.isPending ? (
        <div className="h-8 w-64 animate-pulse rounded bg-ink-100" />
      ) : runs.isError ? (
        <p className="text-xs text-red-700">
          {runs.error instanceof ApiError ? runs.error.message : "Could not load runs."}
        </p>
      ) : completed.length === 0 ? (
        <p className="text-xs text-ink-500">
          No completed runs yet — run a measurement first (Runs tab).
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

/** Catalogue search + pick for one unmapped measurement (the mapping is the
 * human's decision; the server prices + audits it). */
function MapUnmappedRow({
  exception,
  measurement,
  regionCode,
  onMapped,
}: {
  exception: ExceptionRow;
  measurement: Measurement | null;
  regionCode: string;
  onMapped: () => void;
}) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<CatalogItem | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const results = useQuery({
    queryKey: ["catalog", "search", query, regionCode],
    queryFn: () => api.searchCatalog(query, regionCode),
    enabled: query.trim().length >= 2,
  });

  const map = useMutation({
    mutationFn: () =>
      // Row id — the durable identity is unique PER RUN; a project with two
      // runs of the same drawing would be ambiguous (409, never a guess).
      api.mapMeasurement(measurement!.id, {
        catalogue_item_id: picked!.id,
        reason: reason.trim(),
      }),
    onSuccess: () => {
      setError(null);
      onMapped();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not map."),
  });
  const fieldId = `map-${exception.id.slice(0, 8)}`;
  const valid = picked !== null && reason.trim() !== "";

  return (
    <li className="rounded-md border border-ink-200 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge badge={severityBadge(exception.severity)} />
        <span className="text-xs font-medium text-ink-800">unmapped</span>
        {measurement ? (
          <span className="text-xs text-ink-600">
            {measurement.label} ({measurement.value} {measurement.unit})
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-[11px] text-ink-500">{exception.message}</p>
      {measurement ? (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-search`}>
              Catalogue item
            </label>
            <input
              id={`${fieldId}-search`}
              className="input !w-56 !py-1.5 !text-xs"
              placeholder="Search code or description…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPicked(null);
              }}
            />
            {picked ? (
              <span className="text-[11px] text-emerald-700">
                picked {picked.code} — {picked.description} ({picked.unit})
              </span>
            ) : null}
          </div>
          {query.trim().length >= 2 && !picked ? (
            <div className="w-full">
              {results.isPending ? (
                <p className="text-[11px] text-ink-400">Searching…</p>
              ) : results.isError ? (
                <p className="text-[11px] text-red-700">
                  {results.error instanceof ApiError
                    ? results.error.message
                    : "Search failed."}
                </p>
              ) : (
                <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                  {(results.data?.items ?? []).map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className="w-full rounded px-2 py-1 text-left text-xs hover:bg-ink-50"
                        onClick={() => setPicked(item)}
                      >
                        <span className="font-mono text-[10px] text-ink-500">
                          {item.code}
                        </span>{" "}
                        {item.description}{" "}
                        <span className="text-ink-400">({item.unit})</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
          <div className="grow">
            <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-reason`}>
              Reason (required, audited)
            </label>
            <input
              id={`${fieldId}-reason`}
              className="input !w-64 !py-1.5 !text-xs"
              placeholder="e.g. gross area bills the footprint line"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 !text-xs"
            disabled={!valid || map.isPending}
            onClick={() => map.mutate()}
          >
            {map.isPending ? "Mapping…" : "Map"}
          </button>
        </div>
      ) : (
        <p className="mt-1 text-[11px] text-ink-400">
          The measurement row could not be paired — resolve this exception
          directly.
        </p>
      )}
      {error ? <p className="mt-1 text-xs text-red-700">{error}</p> : null}
    </li>
  );
}

/** "Map unmapped" panel: the selected BOQ's source-run blockers, each with a
 * catalogue search + reason + Map. The mapped line appears in the BOQ on
 * success and the blocker clears. */
function MapUnmappedPanel({
  boq,
  projectId,
  regionCode,
}: {
  boq: Boq;
  projectId: string;
  regionCode: string;
}) {
  const qc = useQueryClient();
  const runId = boq.from_run_id;
  // The blocker set is APPROVAL-GATE state: a stale list here would render
  // "no unmapped" while the run's blockers still refuse approval — the
  // believable-but-wrong surface the doctrine forbids. staleTime 0 makes
  // every mount refetch (the global 15s freshness must not apply here;
  // the Runs tab's exceptions cache may predate the BOQ build that
  // persisted these blockers).
  const exceptions = useQuery({
    queryKey: ["exceptions", runId],
    queryFn: () => api.listExceptions(runId!),
    enabled: runId !== null,
    staleTime: 0,
  });
  const measurements = useQuery({
    queryKey: ["measurements", runId],
    queryFn: () => api.listMeasurements(runId!),
    enabled: runId !== null,
    staleTime: 0,
  });

  const pairs = useMemo(() => {
    const exs = exceptions.data?.items ?? [];
    const byMessage = new Map<string, Measurement>();
    for (const m of measurements.data?.items ?? []) {
      byMessage.set(unmappedExceptionMessage(m), m);
    }
    return exs
      .filter((e) => e.code === "unmapped_measurement" && !e.resolved_at)
      .map((e) => ({ exception: e, measurement: byMessage.get(e.message) ?? null }));
  }, [exceptions.data, measurements.data]);

  if (runId === null) return null;
  if (exceptions.isPending || measurements.isPending)
    return <div className="h-16 animate-pulse rounded bg-ink-100" />;
  if (exceptions.isError)
    return (
      <p className="text-xs text-red-700">
        {exceptions.error instanceof ApiError
          ? exceptions.error.message
          : "Could not load blockers."}
      </p>
    );
  if (pairs.length === 0) return null;

  const onMapped = () => {
    void qc.invalidateQueries({ queryKey: ["exceptions", runId] });
    void qc.invalidateQueries({ queryKey: ["boq", boq.id] });
    void qc.invalidateQueries({ queryKey: ["boqs", projectId] });
  };
  return (
    <div className="card p-5">
      <h3 className="mb-1 text-sm font-semibold text-ink-900">Map unmapped</h3>
      <p className="mb-3 text-xs text-ink-500">
        These measured quantities have no catalogue item — approval blocks
        until a human maps them (each mapping is audited and appends the
        priced line to this DRAFT BOQ).
      </p>
      <ul className="space-y-2">
        {pairs.map(({ exception, measurement }) => (
          <MapUnmappedRow
            key={exception.id}
            exception={exception}
            measurement={measurement}
            regionCode={regionCode}
            onMapped={onMapped}
          />
        ))}
      </ul>
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

/** Export card for an approved BOQ (polls the export job). Format is a
 * human choice: csv | xlsx | pdf (the export job validates server-side). */
function BoqExportCard({ boqId }: { boqId: string }) {
  const qc = useQueryClient();
  const [format, setFormat] = useState<"csv" | "xlsx" | "pdf">("csv");
  const [error, setError] = useState<string | null>(null);
  const [activeExportId, setActiveExportId] = useState<string | null>(null);

  const createExport = useMutation({
    mutationFn: () => api.createExport(boqId, format),
    onSuccess: (res) => {
      setError(null);
      setActiveExportId(res.export_id);
      // The Exports tab lists from the server now — refresh its data too.
      void qc.invalidateQueries({ queryKey: ["exports", boqId] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not start export."),
  });

  // Poll the active export until its job reaches a terminal status.
  const activeExport = useQuery({
    queryKey: ["export", activeExportId],
    queryFn: () => api.getExport(activeExportId!),
    enabled: activeExportId !== null,
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 1500 : false,
  });
  const rec = activeExport.data;

  return (
    <div className="border-t border-ink-200 pt-3">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor={`export-format-${boqId.slice(0, 8)}`}>
            Format
          </label>
          <select
            id={`export-format-${boqId.slice(0, 8)}`}
            className="input !w-28 !py-1.5 !text-xs"
            value={format}
            onChange={(e) => setFormat(e.target.value as "csv" | "xlsx" | "pdf")}
          >
            <option value="csv">CSV</option>
            <option value="xlsx">XLSX</option>
            <option value="pdf">PDF</option>
          </select>
        </div>
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 !text-xs"
          disabled={createExport.isPending}
          onClick={() => createExport.mutate()}
        >
          {createExport.isPending ? "Starting…" : `Export ${format.toUpperCase()}`}
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
                  <>
                    <a
                      className="font-medium text-accent-700 hover:underline"
                      href={rec.download_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Download
                    </a>
                    {rec.provenance_download_url ? (
                      <a
                        className="font-medium text-accent-700 hover:underline"
                        href={rec.provenance_download_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Provenance
                      </a>
                    ) : null}
                  </>
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
  const regionCode = project.data?.region_code ?? "";

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

      {/* The mapping panel is DRAFT-only surface: a past-DRAFT BOQ refuses
          edits server-side (reject first — that refusal surfaces honestly). */}
      {boq.data && boq.data.status === "draft" ? (
        <MapUnmappedPanel
          boq={boq.data}
          projectId={projectId}
          regionCode={regionCode}
        />
      ) : null}
    </div>
  );
}
