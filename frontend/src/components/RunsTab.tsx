import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type EvidenceResponse,
  type ExceptionRow,
  type Measurement,
  type Sheet,
} from "../lib/apiClient";
import { measurementStateBadge, runStatusBadge, severityBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";
import GeometryViewer, { type ViewerGeometry } from "./GeometryViewer";
import { useRunPoll, isTerminalRunStatus } from "../lib/jobPolling";
import { useRunStore } from "../stores/runs";

export { useSheets } from "./useSheets";

interface SheetOption {
  drawingId: string;
  filename: string;
  sheet: Sheet;
}

/** Guard: only modelspace sheets with human-confirmed scale are measurable. */
function isModelspaceConfirmed(sheet: Sheet): boolean {
  return sheet.is_modelspace && sheet.calibration?.status === "confirmed";
}

/** "New run" card: pick a parsed drawing's modelspace sheet + max wall thickness. */
function NewRunCard({
  projectId,
  onStarted,
}: {
  projectId: string;
  onStarted: (runId: string) => void;
}) {
  const qc = useQueryClient();
  const drawings = useQuery({
    queryKey: ["drawings", projectId],
    queryFn: () => api.listDrawings(projectId),
  });

  const [drawingId, setDrawingId] = useState<string | null>(null);
  const [sheetId, setSheetId] = useState<string>("");
  const [thickness, setThickness] = useState("250");
  const [error, setError] = useState<string | null>(null);

  // Fetch details for parsed drawings so we can list modelspace sheets with
  // confirmed scale (fetch-all under one stable key).
  const parsedDrawings = useMemo(
    () =>
      (drawings.data?.items ?? []).filter((d) => d.parse_status === "parsed"),
    [drawings.data],
  );
  const parsedIds = useMemo(() => parsedDrawings.map((d) => d.id), [parsedDrawings]);

  const details = useQuery({
    queryKey: ["drawings", "details", ...parsedIds],
    queryFn: async () => Promise.all(parsedIds.map((id) => api.getDrawing(id))),
    enabled: parsedIds.length > 0,
    staleTime: 15_000,
  });

  // Sheets for the selected drawing only (or all when none selected).
  const sheetOptions: SheetOption[] = useMemo(() => {
    const all = details.data ?? [];
    return all
      .filter((d) => drawingId === null || d.id === drawingId)
      .flatMap((d) =>
        d.sheets
          .filter(isModelspaceConfirmed)
          .map((s) => ({ drawingId: d.id, filename: d.filename, sheet: s })),
      );
  }, [details.data, drawingId]);

  // Default to the first parsed drawing / first eligible sheet once loaded.
  useEffect(() => {
    if (drawingId === null && parsedDrawings.length > 0) {
      setDrawingId(parsedDrawings[0].id);
      setSheetId("");
    }
  }, [parsedDrawings, drawingId]);
  useEffect(() => {
    if (sheetId === "" && sheetOptions.length > 0) {
      setSheetId(sheetOptions[0].sheet.id);
    } else if (sheetId !== "" && !sheetOptions.some((o) => o.sheet.id === sheetId)) {
      setSheetId(sheetOptions[0]?.sheet.id ?? "");
    }
  }, [sheetOptions, sheetId]);

  const addRun = useRunStore((s) => s.addRun);
  const createRun = useMutation({
    mutationFn: () =>
      api.createRun(projectId, {
        drawing_file_id: drawingId!,
        sheet_id: sheetId,
        options: { max_wall_thickness: Number(thickness) },
      }),
    onSuccess: (res) => {
      setError(null);
      addRun({
        id: res.run_id,
        status: "queued",
        stats: null,
        error: null,
        created_at: new Date().toISOString(),
      });
      qc.invalidateQueries({ queryKey: ["run", res.run_id] });
      onStarted(res.run_id);
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not start the run."),
  });

  const valid = drawingId !== null && sheetId !== "" && Number(thickness) >= 1;

  return (
    <div className="card p-5">
      <h2 className="mb-1 text-sm font-semibold text-ink-900">New run</h2>
      <p className="mb-3 text-xs text-ink-500">
        Only modelspace sheets with scale you confirmed are measurable — the human
        gate is enforced before any measurement.
      </p>
      {parsedDrawings.length === 0 ? (
        <p className="text-xs text-ink-500">
          No parsed drawings yet — upload and parse one on the Drawings tab first.
        </p>
      ) : details.isPending ? (
        <div className="h-8 w-64 animate-pulse rounded bg-ink-100" />
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor="run-drawing">
              Drawing
            </label>
            <select
              id="run-drawing"
              className="input !w-52 !py-1.5 !text-xs"
              value={drawingId ?? ""}
              onChange={(e) => {
                setDrawingId(e.target.value || null);
                setSheetId("");
              }}
            >
              {parsedDrawings.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.filename}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor="run-sheet">
              Sheet (scale confirmed)
            </label>
            <select
              id="run-sheet"
              className="input !w-44 !py-1.5 !text-xs"
              value={sheetId}
              onChange={(e) => setSheetId(e.target.value)}
            >
              {sheetOptions.length === 0 ? (
                <option value="">No confirmed-scale modelspace sheets</option>
              ) : (
                sheetOptions.map((o) => (
                  <option key={o.sheet.id} value={o.sheet.id}>
                    {o.sheet.sheet_ref}
                  </option>
                ))
              )}
            </select>
          </div>
          <div>
            <label className="label !mb-0.5 !text-[11px]" htmlFor="run-thickness">
              Max wall thickness (mm)
            </label>
            <input
              id="run-thickness"
              className="input !w-40 !py-1.5 !text-xs"
              type="number"
              min={1}
              value={thickness}
              onChange={(e) => setThickness(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 !text-xs"
            disabled={!valid || createRun.isPending}
            onClick={() => createRun.mutate()}
          >
            {createRun.isPending ? "Starting…" : "Start run"}
          </button>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** Resolve-an-exception inline form. */
function ResolveExceptionForm({
  exception,
  onResolved,
}: {
  exception: ExceptionRow;
  onResolved: () => void;
}) {
  const [resolution, setResolution] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const resolve = useMutation({
    mutationFn: () =>
      api.resolveException(exception.id, {
        resolution,
        ...(note.trim() !== "" ? { note } : {}),
      }),
    onSuccess: onResolved,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not resolve."),
  });
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <div>
        <label className="label !mb-0.5 !text-[11px]">Resolution</label>
        <input
          className="input !w-56 !py-1.5 !text-xs"
          placeholder="e.g. wall_pair_confirmed"
          value={resolution}
          onChange={(e) => setResolution(e.target.value)}
        />
      </div>
      <div>
        <label className="label !mb-0.5 !text-[11px]">Note (optional)</label>
        <input
          className="input !w-56 !py-1.5 !text-xs"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      <button
        type="button"
        className="btn-secondary !px-3 !py-1.5 !text-xs"
        disabled={resolution.trim() === "" || resolve.isPending}
        onClick={() => resolve.mutate()}
      >
        {resolve.isPending ? "Resolving…" : "Resolve"}
      </button>
      {error ? <p className="w-full text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** Exceptions list with color-coded severity + inline resolve. */
function ExceptionsPanel({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const [resolving, setResolving] = useState<string | null>(null);
  const exceptions = useQuery({
    queryKey: ["exceptions", runId],
    queryFn: () => api.listExceptions(runId),
  });

  if (exceptions.isPending)
    return <div className="h-16 animate-pulse rounded bg-ink-100" />;
  if (exceptions.isError)
    return (
      <p className="text-xs text-red-700">
        {exceptions.error instanceof ApiError
          ? exceptions.error.message
          : "Could not load exceptions."}
      </p>
    );

  const items = exceptions.data?.items ?? [];
  if (items.length === 0)
    return <p className="text-xs text-ink-500">No exceptions — clean run.</p>;

  return (
    <ul className="space-y-2">
      {items.map((ex) => (
        <li key={ex.id} className="rounded-md border border-ink-200 p-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge badge={severityBadge(ex.severity)} />
            <span className="text-xs font-medium text-ink-800">{ex.code}</span>
            {ex.resolved_at ? (
              <span className="text-[11px] text-emerald-700">
                resolved ({ex.resolution ?? "—"})
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-ink-600">{ex.message}</p>
          {!ex.resolved_at ? (
            resolving === ex.id ? (
              <ResolveExceptionForm
                exception={ex}
                onResolved={() => {
                  setResolving(null);
                  qc.invalidateQueries({ queryKey: ["exceptions", runId] });
                }}
              />
            ) : (
              <button
                type="button"
                className="mt-1.5 text-xs font-medium text-accent-700 hover:underline"
                onClick={() => setResolving(ex.id)}
              >
                Resolve…
              </button>
            )
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** Measurements table: every quantity row carries its state badge. */
export function MeasurementsTable({
  measurements,
  onHighlight,
  selectedId,
}: {
  measurements: Measurement[];
  onHighlight: (m: Measurement) => void;
  selectedId: string | null;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-ink-200 text-[11px] uppercase text-ink-400">
            <th className="py-2 pr-3 font-medium">Label</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium">Quantity</th>
            <th className="py-2 pr-3 font-medium">State</th>
            <th className="py-2 pr-3 font-medium">Rule</th>
            <th className="py-2 pr-3 font-medium">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {measurements.map((m) => (
            <tr
              key={m.id}
              className={
                "cursor-pointer border-b border-ink-100 hover:bg-ink-50 " +
                (selectedId === m.id ? "bg-accent-50" : "")
              }
              onClick={() => onHighlight(m)}
            >
              <td className="py-2 pr-3 font-medium text-ink-800">{m.label}</td>
              <td className="py-2 pr-3 text-ink-500">{m.quantity_type}</td>
              <td className="py-2 pr-3 text-ink-900">
                {m.value} {m.unit}
              </td>
              <td className="py-2 pr-3">
                <StatusBadge badge={measurementStateBadge(m.state)} />
              </td>
              <td className="py-2 pr-3 font-mono text-[10px] text-ink-400">{m.rule_id}</td>
              <td className="py-2 pr-3 text-ink-500">{m.evidence.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Result panel: stats + measurements + exceptions + evidence viewer. */
export function RunResultPanel({ runId }: { runId: string }) {
  const viewerRef = useRef<HTMLDivElement | null>(null);
  const run = useRunPoll(runId);
  const measurements = useQuery({
    queryKey: ["measurements", runId],
    queryFn: () => api.listMeasurements(runId),
    enabled: run.data !== undefined && isTerminalRunStatus(run.data.status),
  });

  // Viewer state: accumulate evidence per measurement so previously selected
  // geometry stays visible (blue) while the newest selection is highlighted red.
  const [evidenceById, setEvidenceById] = useState<Record<string, EvidenceResponse>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setEvidenceError(null);
    api
      .getEvidence(selectedId)
      .then((ev) => {
        if (!cancelled) setEvidenceById((prev) => ({ ...prev, [selectedId]: ev }));
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setEvidenceError(
            err instanceof ApiError ? err.message : "Could not load evidence.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const viewerGeometries: ViewerGeometry[] = useMemo(() => {
    const out: ViewerGeometry[] = [];
    for (const [mid, ev] of Object.entries(evidenceById)) {
      const highlighted = mid === selectedId;
      for (let i = 0; i < ev.geometry.length; i++) {
        const g = ev.geometry[i];
        out.push({
          id: `${mid}#${i}`,
          geom_type: g.geom_type,
          coordinates: g.coordinates,
          layer: g.layer,
          highlighted,
        });
      }
    }
    return out;
  }, [evidenceById, selectedId]);

  const selected = measurements.data?.items.find(
    (m) => m.measurement_id === selectedId || m.id === selectedId,
  );

  if (run.isPending) return <div className="card h-24 animate-pulse bg-ink-100" />;
  if (run.isError)
    return (
      <div className="card p-5 text-sm text-red-700">
        {run.error instanceof ApiError ? run.error.message : "Could not load the run."}
      </div>
    );
  const r = run.data!;

  return (
    <div className="space-y-4" ref={viewerRef}>
      <div className="card p-5">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <h2 className="text-sm font-semibold text-ink-900">Run result</h2>
          <StatusBadge badge={runStatusBadge(r.status)} />
        </div>
        {r.error ? (
          <p className="mb-2 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
            {r.error}
          </p>
        ) : null}
        {r.stats ? (
          <p className="flex gap-4 text-xs text-ink-600">
            <span>
              <strong className="text-ink-900">{r.stats.measured}</strong> measured
            </span>
            <span>
              <strong className="text-ink-900">{r.stats.blocked}</strong> blocked
            </span>
            <span>
              <strong className="text-ink-900">{r.stats.exceptions}</strong> exceptions
            </span>
          </p>
        ) : null}
      </div>

      {isTerminalRunStatus(r.status) && r.status !== "failed" ? (
        <>
          <div className="card p-5">
            <h3 className="mb-3 text-sm font-semibold text-ink-900">Measurements</h3>
            {measurements.isPending ? (
              <div className="h-24 animate-pulse rounded bg-ink-100" />
            ) : measurements.isError ? (
              <p className="text-xs text-red-700">
                {measurements.error instanceof ApiError
                  ? measurements.error.message
                  : "Could not load measurements."}
              </p>
            ) : (measurements.data?.items.length ?? 0) === 0 ? (
              <p className="text-xs text-ink-500">No measurements produced.</p>
            ) : (
              <MeasurementsTable
                measurements={measurements.data.items}
                selectedId={selectedId}
                onHighlight={(m) => {
                  setSelectedId(m.measurement_id);
                  // Scroll the viewer into view when a row is clicked.
                  viewerRef.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "nearest",
                  });
                }}
              />
            )}
          </div>

          <div className="card p-5">
            <h3 className="mb-3 text-sm font-semibold text-ink-900">Exceptions</h3>
            <ExceptionsPanel runId={runId} />
          </div>

          <GeometryViewer
            geometries={viewerGeometries}
            centerlines={selected?.centerline ? [selected.centerline] : []}
            emptyHint={
              evidenceError
                ? evidenceError
                : "Select a measurement row to load its evidence geometry."
            }
          />
        </>
      ) : null}
    </div>
  );
}

export default function RunsTab({ projectId }: { projectId: string }) {
  const runs = useRunStore((s) => s.runs);
  const updateRun = useRunStore((s) => s.updateRun);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  // Keep the session store fresh: mirror polled run state into it.
  const activeRun = useRunPoll(activeRunId);
  useEffect(() => {
    const r = activeRun.data;
    if (r && isTerminalRunStatus(r.status)) {
      updateRun(r.id, { status: r.status, stats: r.stats, error: r.error });
    }
  }, [activeRun.data, updateRun]);

  return (
    <div className="space-y-4">
      <NewRunCard projectId={projectId} onStarted={setActiveRunId} />
      {runs.length > 0 ? (
        <div className="card p-4">
          <h2 className="mb-2 text-sm font-semibold text-ink-900">Runs this session</h2>
          <ul className="divide-y divide-ink-100">
            {runs.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2">
                <button
                  type="button"
                  className={
                    "text-left text-xs " +
                    (activeRunId === r.id
                      ? "font-semibold text-accent-700"
                      : "text-ink-700 hover:text-accent-700")
                  }
                  onClick={() => setActiveRunId(r.id)}
                >
                  Run {r.id.slice(0, 8)}…
                  {r.stats ? (
                    <span className="ml-2 text-ink-400">
                      {r.stats.measured} measured / {r.stats.exceptions} exceptions
                    </span>
                  ) : null}
                </button>
                <StatusBadge badge={runStatusBadge(r.status)} />
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-ink-400">
            Run history across sessions arrives with the runs list endpoint.
          </p>
        </div>
      ) : null}
      {activeRunId ? <RunResultPanel runId={activeRunId} /> : null}
    </div>
  );
}
