import { useExportStore } from "../stores/exports";
import { exportStatusBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";

/**
 * Exports this session. There is no list-exports endpoint in the v1 API
 * contract, so this view is honest about its scope: full export history
 * arrives with the T104 artifacts view.
 */
export default function ExportsTab() {
  const exports = useExportStore((s) => s.exports);

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink-900">Exports (this session)</h2>
        <p className="mb-4 text-xs text-ink-500">
          Exports you create while this browser session is open. Full export
          history arrives with the artifacts view (T104).
        </p>
        {exports.length === 0 ? (
          <p className="text-xs text-ink-500">
            No exports yet — approve a BOQ on the BOQ tab, then export it as CSV.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-ink-200 text-[11px] uppercase text-ink-400">
                  <th className="py-2 pr-3 font-medium">Export</th>
                  <th className="py-2 pr-3 font-medium">BOQ</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">SHA-256</th>
                  <th className="py-2 pr-3 font-medium">Download</th>
                  <th className="py-2 pr-3 font-medium">Created</th>
                </tr>
              </thead>
              <tbody>
                {exports.map((e) => (
                  <tr key={e.id} className="border-b border-ink-100">
                    <td className="py-2 pr-3 font-mono text-[10px] text-ink-500">
                      {e.id.slice(0, 8)}…
                    </td>
                    <td className="py-2 pr-3 font-mono text-[10px] text-ink-500">
                      {e.boq_id.slice(0, 8)}…
                    </td>
                    <td className="py-2 pr-3">
                      <StatusBadge badge={exportStatusBadge(e.status)} />
                    </td>
                    <td className="py-2 pr-3 font-mono text-[10px] text-ink-400">
                      {e.sha256 ? `${e.sha256.slice(0, 16)}…` : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {e.status === "succeeded" && e.download_url ? (
                        <a
                          className="font-medium text-accent-700 hover:underline"
                          href={e.download_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Download
                        </a>
                      ) : (
                        <span className="text-ink-400">—</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-ink-500">
                      {new Date(e.created_at).toLocaleTimeString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <p className="text-[11px] text-ink-400">
        Every export is approval-gated server-side: only an approved BOQ can be
        exported, and the manifest pins the content digest.
      </p>
    </div>
  );
}
