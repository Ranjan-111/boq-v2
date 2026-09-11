import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type AiSuggestionRow,
  type ElementRow,
  type EvidenceResponse,
  type ExceptionRow,
  type Measurement,
  type Sheet,
} from "../lib/apiClient";
import {
  measurementStateBadge,
  runStatusBadge,
  severityBadge,
  correctedBadge,
  typeSourceBadge,
} from "../lib/statusBadges";
import {
  ELEMENT_TYPES,
  confidencePercent,
  groupElementsByMeasurements,
  isApplyableSuggestion,
  isReviewable,
  quantityDisplay,
  suggestionSummary,
} from "../lib/reviewHelpers";
import type { ElementGroup } from "../lib/reviewHelpers";
import StatusBadge from "./StatusBadge";
import GeometryViewer, { type ViewerGeometry } from "./GeometryViewer";
import {
  isJobActive,
  isTerminalJobStatus,
  isTerminalRunStatus,
  useJobPoll,
  useRunPoll,
} from "../lib/jobPolling";

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

  const createRun = useMutation({
    mutationFn: () =>
      api.createRun(projectId, {
        drawing_file_id: drawingId!,
        sheet_id: sheetId,
        options: { max_wall_thickness: Number(thickness) },
      }),
    onSuccess: (res) => {
      setError(null);
      // The runs list is server-backed now — the new run appears on refetch.
      void qc.invalidateQueries({ queryKey: ["runs", projectId] });
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

/** Correct-an-exception inline form (value + reason, both required). */
function CorrectMeasurementForm({
  measurement,
  onDone,
}: {
  measurement: Measurement;
  onDone: () => void;
}) {
  const [value, setValue] = useState(measurement.corrected_value ?? measurement.value);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const correct = useMutation({
    mutationFn: () =>
      // Row id (globally unique PK), not the durable identity — the
      // identity is unique PER RUN, so a project with two runs of the
      // same drawing would be ambiguous (409, not a guess).
      api.reviewMeasurement(measurement.id, {
        action: "correct",
        value: value.trim(),
        reason: reason.trim(),
      }),
    onSuccess: onDone,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not correct."),
  });
  const valid = value.trim() !== "" && Number(value) >= 0 && reason.trim() !== "";
  // A measurement may be corrected more than once (re-review); the form
  // unmounts between edits, so a per-measurement id stays unique in the DOM.
  const fieldId = `correct-${measurement.measurement_id}`;
  return (
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <div>
        <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-value`}>
          Corrected value ({measurement.unit})
        </label>
        <input
          id={`${fieldId}-value`}
          className="input !w-36 !py-1.5 !text-xs"
          inputMode="decimal"
          placeholder="e.g. 6.500000"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </div>
      <div className="grow">
        <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-reason`}>
          Reason (required, audited)
        </label>
        <input
          id={`${fieldId}-reason`}
          className="input !w-64 !py-1.5 !text-xs"
          placeholder="e.g. site tape measured 2 m more"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <button
        type="button"
        className="btn-primary !px-3 !py-1.5 !text-xs"
        disabled={!valid || correct.isPending}
        onClick={() => correct.mutate()}
      >
        {correct.isPending ? "Saving…" : "Save correction"}
      </button>
      <button
        type="button"
        className="btn-secondary !px-3 !py-1.5 !text-xs"
        onClick={onDone}
      >
        Cancel
      </button>
      {error ? <p className="w-full text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** One measurement row: quantity cell + inline Accept / Correct actions. */
function MeasurementRowActions({
  measurement,
  onCorrected,
}: {
  measurement: Measurement;
  onCorrected: () => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const accept = useMutation({
    mutationFn: () =>
      // Row id, not durable identity — see the correct mutation above.
      api.reviewMeasurement(measurement.id, {
        action: "accept",
        reason: "accepted in review workspace",
      }),
    onSuccess: onCorrected,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not accept."),
  });
  if (!isReviewable(measurement.state)) return null;
  return (
    <div className="flex items-center gap-3">
      {correcting ? (
        <CorrectMeasurementForm
          measurement={measurement}
          onDone={() => {
            setCorrecting(false);
            onCorrected();
          }}
        />
      ) : (
        <>
          <button
            type="button"
            className="text-xs font-medium text-accent-700 hover:underline"
            disabled={accept.isPending}
            onClick={(e) => {
              e.stopPropagation();
              accept.mutate();
            }}
          >
            {accept.isPending ? "Accepting…" : "Accept"}
          </button>
          <button
            type="button"
            className="text-xs font-medium text-accent-700 hover:underline"
            onClick={(e) => {
              e.stopPropagation();
              setError(null);
              setCorrecting(true);
            }}
          >
            Correct…
          </button>
        </>
      )}
      {error ? <span className="text-[11px] text-red-700">{error}</span> : null}
    </div>
  );
}

/** Measurements table: every quantity row carries its state badge; the
 * original engine value stays visible (strikethrough) beside a correction. */
export function MeasurementsTable({
  measurements,
  onHighlight,
  selectedId,
  onReviewed,
}: {
  measurements: Measurement[];
  onHighlight: (m: Measurement) => void;
  selectedId: string | null;
  onReviewed: () => void;
}) {
  const display = (m: Measurement) => quantityDisplay(m);
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
            <th className="py-2 pr-3 font-medium">Review</th>
          </tr>
        </thead>
        <tbody>
          {measurements.map((m) => {
            const q = display(m);
            return (
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
                  {q.correctedPill ? (
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="line-through decoration-ink-300 text-ink-400">
                        {q.original}
                      </span>
                      <span className="font-medium">{q.corrected}</span>
                      <StatusBadge badge={correctedBadge()} />
                    </span>
                  ) : (
                    <span>
                      {q.original} {q.unit}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3">
                  <StatusBadge badge={measurementStateBadge(m.state)} />
                </td>
                <td className="py-2 pr-3 font-mono text-[10px] text-ink-400">{m.rule_id}</td>
                <td className="py-2 pr-3 text-ink-500">{m.evidence.length}</td>
                <td className="py-2 pr-3" onClick={(e) => e.stopPropagation()}>
                  <MeasurementRowActions measurement={m} onCorrected={onReviewed} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** One element row: type badge + dropdown + reason, saved through the audited
 * override endpoint; the type_source badge flips to "human set" on save. */
function ElementOverrideRow({ element }: { element: ElementGroup }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [elementType, setElementType] = useState<string>(element.element_type);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const override = useMutation({
    mutationFn: () =>
      api.overrideClassification(element.element_id, {
        element_type: elementType as ElementRow["element_type"],
        reason: reason.trim(),
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      setReason("");
      // Refetch the measurement list — the elements panel derives from it.
      void qc.invalidateQueries({ queryKey: ["measurements"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not override."),
  });
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-ink-800">
          {element.label ?? element.element_id.slice(0, 8)}
        </span>
        <StatusBadge badge={typeSourceBadge(element.type_source)} />
        <span className="text-xs text-ink-500">{element.element_type}</span>
        <span className="text-[11px] text-ink-400">
          {element.measurement_count} measurements
        </span>
        <button
          type="button"
          className="text-xs font-medium text-accent-700 hover:underline"
          onClick={() => setOpen(!open)}
        >
          {open ? "Close" : "Override…"}
        </button>
      </div>
      {open ? (
        <div className="mt-1.5 flex flex-wrap items-end gap-2">
          <div>
            <label className="label !mb-0.5 !text-[11px]">Type</label>
            <select
              className="input !w-36 !py-1.5 !text-xs"
              value={elementType}
              onChange={(e) => setElementType(e.target.value)}
            >
              {ELEMENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="grow">
            <label className="label !mb-0.5 !text-[11px]">Reason (audited)</label>
            <input
              className="input !w-64 !py-1.5 !text-xs"
              placeholder="e.g. this layer is actually a room boundary"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 !text-xs"
            disabled={reason.trim() === "" || override.isPending}
            onClick={() => override.mutate()}
          >
            {override.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      ) : null}
      {error ? <p className="mt-1 text-xs text-red-700">{error}</p> : null}
    </li>
  );
}

/** Elements of the run (derived from the measurements list — V1 has no
 * separate element endpoint): one override affordance per element row. */
function ElementsPanel({ measurements }: { measurements: Measurement[] }) {
  const elements = useMemo(
    () => groupElementsByMeasurements(measurements),
    [measurements],
  );
  if (elements.length === 0) return null;
  return (
    <div className="card p-5">
      <h3 className="mb-1 text-sm font-semibold text-ink-900">Elements</h3>
      <p className="mb-2 text-[11px] text-ink-400">
        A human override is audited and never erases the AI/geometry provenance.
      </p>
      <ul className="divide-y divide-ink-100">
        {elements.map((el) => (
          <ElementOverrideRow key={el.element_id} element={el} />
        ))}
      </ul>
    </div>
  );
}

/** Apply form for one element_classification suggestion (reason required —
 * the apply is an audited human decision, never a one-click accept). */
function ApplySuggestionForm({
  suggestionId,
  onApplied,
}: {
  suggestionId: string;
  onApplied: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const apply = useMutation({
    mutationFn: () => api.applySuggestion(suggestionId, { reason: reason.trim() }),
    onSuccess: () => {
      setError(null);
      onApplied();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not apply."),
  });
  const fieldId = `apply-${suggestionId.slice(0, 8)}`;
  return (
    <div className="mt-1.5 flex flex-wrap items-end gap-2">
      <div className="grow">
        <label className="label !mb-0.5 !text-[11px]" htmlFor={`${fieldId}-reason`}>
          Reason (required, audited)
        </label>
        <input
          id={`${fieldId}-reason`}
          className="input !w-64 !py-1.5 !text-xs"
          placeholder="e.g. the geometry reads as a room boundary"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <button
        type="button"
        className="btn-primary !px-3 !py-1.5 !text-xs"
        disabled={reason.trim() === "" || apply.isPending}
        onClick={() => apply.mutate()}
      >
        {apply.isPending ? "Applying…" : "Apply"}
      </button>
      {error ? <p className="w-full text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** One advisory suggestion row: type, honest confidence, payload summary —
 * an Apply affordance only for the classification kind. */
function SuggestionRow({
  suggestion,
  onApplied,
}: {
  suggestion: AiSuggestionRow;
  onApplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700">
          {suggestion.suggestion_type}
        </span>
        {/* The stub proposes at 5% — the UI shows that weakness, always. */}
        <span className="text-xs font-medium text-ink-700">
          {confidencePercent(suggestion.confidence)} confidence
        </span>
        <span className="text-[11px] text-ink-400">{suggestion.model}</span>
        {suggestion.accepted ? (
          <span className="text-[11px] text-emerald-700">applied</span>
        ) : null}
        {!suggestion.accepted && isApplyableSuggestion(suggestion.suggestion_type) ? (
          <button
            type="button"
            className="text-xs font-medium text-accent-700 hover:underline"
            onClick={() => setOpen(!open)}
          >
            {open ? "Close" : "Apply…"}
          </button>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-ink-600">{suggestionSummary(suggestion)}</p>
      {open ? (
        <ApplySuggestionForm suggestionId={suggestion.id} onApplied={onApplied} />
      ) : null}
    </li>
  );
}

/** AI insights for the selected run: the advisory rows (read-only) with the
 * audited apply for classification proposals. The analyze pass is a separate
 * job — the button kicks it and polls the job id. */
function AiInsightsPanel({ runId, projectId }: { runId: string; projectId: string }) {
  const qc = useQueryClient();
  const [analyzeJobId, setAnalyzeJobId] = useState<string | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const insights = useQuery({
    queryKey: ["insights", runId],
    queryFn: () => api.getAiInsights(runId),
  });
  const job = useJobPoll(analyzeJobId);

  const analyze = useMutation({
    mutationFn: () => api.startAnalyze(runId),
    onSuccess: (res) => {
      setAnalyzeError(null);
      setAnalyzeJobId(res.job_id);
    },
    onError: (err) =>
      setAnalyzeError(err instanceof ApiError ? err.message : "Could not analyze."),
  });

  useEffect(() => {
    if (job.data && isTerminalJobStatus(job.data.status)) {
      setAnalyzeJobId(null);
      void qc.invalidateQueries({ queryKey: ["insights", runId] });
    }
  }, [job.data, qc, runId]);

  const onApplied = () => {
    // The apply changed an element's type — the elements panel derives from
    // the measurements list, so both surfaces refetch.
    void qc.invalidateQueries({ queryKey: ["insights", runId] });
    void qc.invalidateQueries({ queryKey: ["measurements", runId] });
    void qc.invalidateQueries({ queryKey: ["runs", projectId] });
  };

  return (
    <div className="card p-5">
      <h3 className="mb-1 text-sm font-semibold text-ink-900">AI insights</h3>
      <p className="mb-2 text-[11px] text-ink-400">
        {insights.data?.generated_note ??
          "advisory only — nothing here changes a quantity"}
      </p>
      {insights.isPending ? (
        <div className="h-16 animate-pulse rounded bg-ink-100" />
      ) : insights.isError ? (
        <p className="text-xs text-red-700">
          {insights.error instanceof ApiError
            ? insights.error.message
            : "Could not load insights."}
        </p>
      ) : (insights.data?.suggestions.length ?? 0) === 0 ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-xs text-ink-500">No suggestions yet for this run.</p>
          <button
            type="button"
            className="btn-secondary !px-3 !py-1.5 !text-xs"
            disabled={
              analyze.isPending ||
              isJobActive(analyzeJobId, job.isPending, job.data?.status)
            }
            onClick={() => analyze.mutate()}
          >
            {analyze.isPending || isJobActive(analyzeJobId, job.isPending, job.data?.status)
              ? "Analyzing…"
              : "Run AI analysis"}
          </button>
          {analyzeError ? (
            <p className="w-full text-xs text-red-700">{analyzeError}</p>
          ) : null}
        </div>
      ) : (
        <>
          <ul className="divide-y divide-ink-100">
            {(insights.data?.suggestions ?? []).map((s) => (
              <SuggestionRow key={s.id} suggestion={s} onApplied={onApplied} />
            ))}
          </ul>
          <button
            type="button"
            className="mt-2 text-xs font-medium text-accent-700 hover:underline"
            disabled={
              analyze.isPending ||
              isJobActive(analyzeJobId, job.isPending, job.data?.status)
            }
            onClick={() => analyze.mutate()}
          >
            {analyze.isPending || isJobActive(analyzeJobId, job.isPending, job.data?.status)
              ? "Analyzing…"
              : "Re-run AI analysis"}
          </button>
        </>
      )}
    </div>
  );
}

/** Result panel: stats + measurements + exceptions + evidence viewer. */
export function RunResultPanel({
  runId,
  projectId,
}: {
  runId: string;
  projectId?: string;
}) {
  const qc = useQueryClient();
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

  // A review action (accept/correct) changes rows the panel shows — refetch
  // the measurement list; the effect below re-pulls the selected evidence
  // when its dataUpdatedAt changes.
  const refreshReview = () => {
    void qc.invalidateQueries({ queryKey: ["measurements", runId] });
  };

  // The runs list is server-backed now: when this run reaches a terminal
  // status, refresh the project's list so the new status shows.
  const polledStatus = run.data?.status;
  useEffect(() => {
    if (projectId && polledStatus !== undefined
        && isTerminalRunStatus(polledStatus)) {
      void qc.invalidateQueries({ queryKey: ["runs", projectId] });
    }
  }, [polledStatus, projectId, qc]);

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
  }, [selectedId, measurements.dataUpdatedAt]);

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
                  // Row id — the evidence lookup keys on the same resolver,
                  // so the globally-unique PK keeps it deterministic in
                  // multi-run projects (durable identity is per-run).
                  setSelectedId(m.id);
                  // Scroll the viewer into view when a row is clicked.
                  viewerRef.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "nearest",
                  });
                }}
                onReviewed={refreshReview}
              />
            )}
          </div>

          {measurements.data ? (
            <ElementsPanel measurements={measurements.data.items} />
          ) : null}

          {projectId ? (
            <AiInsightsPanel runId={runId} projectId={projectId} />
          ) : null}

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
  const runs = useQuery({
    queryKey: ["runs", projectId],
    queryFn: () => api.listRuns(projectId),
  });
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  // Keep the list current while a run is executing: poll only while the
  // active run is queued/running (the job-polling idiom).
  const activeRun = useRunPoll(activeRunId);
  const activeStatus = activeRun.data?.status;
  useEffect(() => {
    if (activeRunId === null && runs.data && runs.data.items.length > 0) {
      setActiveRunId(runs.data.items[0].id);
    }
  }, [runs.data, activeRunId]);

  return (
    <div className="space-y-4">
      <NewRunCard projectId={projectId} onStarted={setActiveRunId} />
      <div className="card p-4">
        <h2 className="mb-2 text-sm font-semibold text-ink-900">Runs</h2>
        {runs.isPending ? (
          <div className="h-16 animate-pulse rounded bg-ink-100" />
        ) : runs.isError ? (
          <p className="text-xs text-red-700">
            {runs.error instanceof ApiError
              ? runs.error.message
              : "Could not load runs."}
          </p>
        ) : runs.data && runs.data.items.length > 0 ? (
          <ul className="divide-y divide-ink-100">
            {runs.data.items.map((r) => (
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
                  ) : r.created_at ? (
                    <span className="ml-2 text-ink-400">
                      {new Date(r.created_at).toLocaleDateString()}
                    </span>
                  ) : null}
                </button>
                <StatusBadge badge={runStatusBadge(r.status)} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-ink-500">No runs yet — start one above.</p>
        )}
        {/* The active run's status is polled live (above); when it turns
            terminal the list refetches so the badge flips. */}
        {activeRunId && activeStatus !== undefined
          && !isTerminalRunStatus(activeStatus) ? (
          <p className="mt-2 text-[11px] text-ink-400">
            Run {activeRunId.slice(0, 8)} is {activeStatus}…
          </p>
        ) : null}
      </div>
      {activeRunId ? (
        <RunResultPanel runId={activeRunId} projectId={projectId} />
      ) : null}
    </div>
  );
}
