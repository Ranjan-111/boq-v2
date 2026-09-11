import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type ExportRecord } from "../lib/apiClient";
import { exportStatusBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";

/** Download links for one succeeded export: the list endpoint carries no
 * signed URLs, so the row fetches its export record on demand. */
function DownloadLink({ exportId }: { exportId: string }) {
  const record = useQuery({
    queryKey: ["export", exportId],
    queryFn: () => api.getExport(exportId),
    staleTime: 60_000,
  });
  if (record.isPending) return <span className="text-ink-400">loading…</span>;
  if (record.isError)
    return (
      <span className="text-[11px] text-red-700">
        {record.error instanceof ApiError
          ? record.error.message
          : "Could not load the artifact."}
      </span>
    );
  const rec: ExportRecord | undefined = record.data;
  return rec?.download_url ? (
    <span className="flex flex-wrap gap-2">
      <a className="font-medium text-accent-700 hover:underline" href={rec.download_url}
         target="_blank" rel="noreferrer">Download</a>
      {rec.provenance_download_url ? (
        <a className="font-medium text-accent-700 hover:underline"
           href={rec.provenance_download_url} target="_blank" rel="noreferrer">
          Provenance
        </a>
      ) : null}
    </span>
  ) : <span className="text-ink-400">—</span>;
}

/**
 * The project's exports, listed per BOQ from the server (GET /boqs/{id}/exports).
 * Pending artifacts poll until the export job answers; every row pins the
 * content digest so an artifact is verifiable, not just downloadable.
 */
export default function ExportsTab({ projectId }: { projectId: string }) {
  const boqs = useQuery({
    queryKey: ["boqs", projectId],
    queryFn: () => api.listBoqs(projectId),
  });
  const [selectedBoqId, setSelectedBoqId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedBoqId === null && boqs.data && boqs.data.items.length > 0) {
      setSelectedBoqId(boqs.data.items[boqs.data.items.length - 1].id);
    }
  }, [boqs.data, selectedBoqId]);

  const exports = useQuery({
    queryKey: ["exports", selectedBoqId],
    queryFn: () => api.listExports(selectedBoqId!),
    enabled: selectedBoqId !== null,
    // Pending artifacts finish asynchronously — poll while any is pending
    // (the job-polling idiom), stop once the page is terminal.
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((e) => e.status === "pending")
        ? 1500
        : false,
  });

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink-900">Exports</h2>
        <p className="mb-4 text-xs text-ink-500">
          The exports of the BOQ you pick — every artifact this project ever
          produced for it, newest first.
        </p>
        {boqs.isPending ? (
          <div className="h-8 w-56 animate-pulse rounded bg-ink-100" />
        ) : boqs.isError ? (
          <p className="text-xs text-red-700">
            {boqs.error instanceof ApiError
              ? boqs.error.message
              : "Could not load the BOQs."}
          </p>
        ) : boqs.data && boqs.data.items.length > 0 ? (
          <>
            <div className="mb-3">
              <label className="label !mb-0.5 !text-[11px]" htmlFor="exports-boq">
                BOQ
              </label>
              <select
                id="exports-boq"
                className="input !w-56 !py-1.5 !text-xs"
                value={selectedBoqId ?? ""}
                onChange={(e) => setSelectedBoqId(e.target.value || null)}
              >
                {boqs.data.items.map((b) => (
                  <option key={b.id} value={b.id}>
                    v{b.version} ({b.status})
                  </option>
                ))}
              </select>
            </div>
            {exports.isPending ? (
              <div className="h-16 animate-pulse rounded bg-ink-100" />
            ) : exports.isError ? (
              <p className="text-xs text-red-700">
                {exports.error instanceof ApiError
                  ? exports.error.message
                  : "Could not load exports."}
              </p>
            ) : (exports.data?.items.length ?? 0) === 0 ? (
              <p className="text-xs text-ink-500">
                No exports for this BOQ yet — approve it on the BOQ tab, then
                export it (CSV, XLSX or PDF).
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-ink-200 text-[11px] uppercase text-ink-400">
                      <th className="py-2 pr-3 font-medium">Export</th>
                      <th className="py-2 pr-3 font-medium">Format</th>
                      <th className="py-2 pr-3 font-medium">Status</th>
                      <th className="py-2 pr-3 font-medium">SHA-256</th>
                      <th className="py-2 pr-3 font-medium">Download</th>
                      <th className="py-2 pr-3 font-medium">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(exports.data?.items ?? []).map((e) => (
                      <tr key={e.id} className="border-b border-ink-100">
                        <td className="py-2 pr-3 font-mono text-[10px] text-ink-500">
                          {e.id.slice(0, 8)}…
                        </td>
                        <td className="py-2 pr-3">
                          <span className="rounded bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium text-ink-600">
                            {e.format.toUpperCase()}
                          </span>
                        </td>
                        <td className="py-2 pr-3">
                          <StatusBadge badge={exportStatusBadge(e.status)} />
                        </td>
                        <td className="py-2 pr-3 font-mono text-[10px] text-ink-400">
                          {e.sha256 ? `${e.sha256.slice(0, 16)}…` : "—"}
                        </td>
                        <td className="py-2 pr-3">
                          {e.status === "succeeded" ? (
                            <DownloadLink exportId={e.id} />
                          ) : (
                            <span className="text-ink-400">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-ink-500">
                          {e.created_at
                            ? new Date(e.created_at).toLocaleString()
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-ink-500">
            No BOQs yet — build one on the BOQ tab first.
          </p>
        )}
      </div>
      <p className="text-[11px] text-ink-400">
        Every export is approval-gated server-side: only an approved BOQ can
        be exported, and the manifest pins the content digest — a download is
        verifiable, not just retrievable.
      </p>
    </div>
  );
}
