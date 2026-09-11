import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, type AuditEntryRow } from "../lib/apiClient";
import {
  actorShort,
  distinctSubjectTypes,
  filterBySubjectType,
  sortAuditRows,
} from "../lib/reviewHelpers";

/**
 * Project audit trail — the review history surface (T075).
 * Lists every audited human decision for this project: action, actor (short),
 * subject, at. Filter by subject_type; refetches on open only (no polling —
 * the trail is append-only and read on demand).
 */
export default function AuditTab({ projectId }: { projectId: string }) {
  const [subjectType, setSubjectType] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [limit] = useState(100);

  // One fetch per (filter, cursor) state — refetchOnWindowFocus stays off
  // globally and there is no refetchInterval: open-only semantics.
  const audit = useQuery({
    queryKey: ["audit", projectId, subjectType, cursor, limit],
    queryFn: () =>
      api.getProjectAudit(projectId, {
        ...(subjectType ? { subject_type: subjectType } : {}),
        limit,
        ...(cursor ? { before: cursor } : {}),
      }),
  });

  const items: AuditEntryRow[] = useMemo(
    () => sortAuditRows(audit.data?.items ?? []),
    [audit.data],
  );
  const visible = useMemo(
    () => filterBySubjectType(items, subjectType),
    [items, subjectType],
  );
  const subjectTypes = useMemo(() => distinctSubjectTypes(items), [items]);

  const nextCursor = audit.data?.next_cursor ?? null;

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="mb-1 text-sm font-semibold text-ink-900">Audit trail</h2>
        <p className="mb-3 text-xs text-ink-500">
          Every audited human decision on this project — corrections, accepts,
          overrides, resolutions. Append-only; loaded on demand.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor="audit-subject">
              Subject type
            </label>
            <select
              id="audit-subject"
              className="input !w-44 !py-1.5 !text-xs"
              value={subjectType}
              onChange={(e) => {
                setSubjectType(e.target.value);
                setCursor(null); // filter change restarts pagination
              }}
            >
              <option value="">All</option>
              {subjectTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn-secondary !px-3 !py-1.5 !text-xs"
            onClick={() => {
              setCursor(null);
              void audit.refetch();
            }}
          >
            {audit.isFetching ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="card p-5">
        {audit.isPending ? (
          <div className="h-24 animate-pulse rounded bg-ink-100" />
        ) : audit.isError ? (
          <p className="text-xs text-red-700">
            {audit.error instanceof ApiError
              ? audit.error.message
              : "Could not load the audit trail."}
          </p>
        ) : visible.length === 0 ? (
          <p className="text-xs text-ink-500">
            No audit entries yet — accept or correct a measurement to start the
            trail.
          </p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-ink-200 text-[11px] uppercase text-ink-400">
                    <th className="py-2 pr-3 font-medium">When</th>
                    <th className="py-2 pr-3 font-medium">Action</th>
                    <th className="py-2 pr-3 font-medium">Actor</th>
                    <th className="py-2 pr-3 font-medium">Subject</th>
                    <th className="py-2 pr-3 font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <tr key={row.id} className="border-b border-ink-100">
                      <td className="py-2 pr-3 text-ink-500">
                        {new Date(row.at).toLocaleString()}
                      </td>
                      <td className="py-2 pr-3 font-mono text-[10px] text-ink-700">
                        {row.action}
                      </td>
                      <td
                        className="py-2 pr-3 font-mono text-[10px] text-ink-400"
                        title={row.actor}
                      >
                        {actorShort(row.actor)}…
                      </td>
                      <td className="py-2 pr-3 text-ink-500">
                        {row.subject_type}
                      </td>
                      <td className="py-2 pr-3 text-ink-600">
                        {row.reason ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex items-center justify-between">
              <p className="text-[11px] text-ink-400">
                {visible.length} shown · ordered newest first
              </p>
              {nextCursor ? (
                <button
                  type="button"
                  className="btn-secondary !px-3 !py-1.5 !text-xs"
                  onClick={() => setCursor(nextCursor)}
                >
                  Older →
                </button>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
