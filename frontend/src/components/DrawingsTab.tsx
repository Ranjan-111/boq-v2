import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type DrawingListItem, type ScaleMethod, type Sheet } from "../lib/apiClient";
import { calibrationBadge, parseStatusBadge } from "../lib/statusBadges";
import StatusBadge from "./StatusBadge";
import { useJobPoll } from "../lib/jobPolling";
import { useSheets } from "./useSheets";

/** Upload card: file picker + Upload → 202 → poll job → refetch list. */
function UploadCard({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<{ job_id: string } | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadDrawing(projectId, file),
    onSuccess: (res) => {
      setUploadError(null);
      setParseError(null);
      // job_id "" = dedupe reuse (already parsed/parsing): no new job exists,
      // so poll nothing — the list invalidate below shows the standing state.
      setAccepted(res.job_id ? { job_id: res.job_id } : null);
      qc.invalidateQueries({ queryKey: ["drawings", projectId] });
    },
    onError: (err) =>
      setUploadError(err instanceof ApiError ? err.message : "Upload failed."),
  });

  const job = useJobPoll(accepted ? accepted.job_id : null);
  // When the parse job finishes, refresh the drawings list and stop polling
  // (done in an effect — never as a render side-effect). The failure message
  // is captured BEFORE clearing the job id, or the poll result would be lost.
  const jobStatus = job.data?.status;
  useEffect(() => {
    if (jobStatus === "succeeded") {
      qc.invalidateQueries({ queryKey: ["drawings", projectId] });
      setAccepted(null);
    } else if (jobStatus === "failed") {
      setParseError(job.data?.error ?? "Parsing failed.");
      qc.invalidateQueries({ queryKey: ["drawings", projectId] });
      setAccepted(null);
    }
  }, [jobStatus, job.data, qc, projectId]);

  function submit() {
    if (selected) upload.mutate(selected);
  }

  return (
    <div className="card p-5">
      <h2 className="mb-1 text-sm font-semibold text-ink-900">Upload a drawing</h2>
      <p className="mb-3 text-xs text-ink-500">
        DXF, PDF, PNG, JPG or WEBP. Parsing runs as a background job — the list
        updates when it finishes.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="file"
          aria-label="Upload drawing file"
          accept=".dxf,.pdf,.png,.jpg,.webp"
          className="text-xs text-ink-600 file:mr-3 file:rounded-md file:border-0 file:bg-ink-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-ink-700 hover:file:bg-ink-200"
          onChange={(e) => setSelected(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 !text-xs"
          disabled={!selected || upload.isPending}
          onClick={submit}
        >
          {upload.isPending ? "Uploading…" : "Upload"}
        </button>
        {accepted ? (
          <span className="text-xs text-blue-700">Parsing… (polling the job)</span>
        ) : null}
      </div>
      {uploadError ? (
        <p className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{uploadError}</p>
      ) : null}
      {parseError ? (
        <p className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          Parse failed: {parseError}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Inline scale confirmation — THE human gate. Copy is explicit: this is the
 * only place scale becomes confirmed; nothing is ever guessed.
 */
function ScaleConfirmForm({ sheet, onDone }: { sheet: Sheet; onDone: () => void }) {
  const [units, setUnits] = useState(
    sheet.calibration?.units_per_drawing_unit ?? "",
  );
  const [method, setMethod] = useState<ScaleMethod>("user_known_ratio");
  const [error, setError] = useState<string | null>(null);

  const confirm = useMutation({
    mutationFn: () =>
      api.confirmScale(sheet.id, { units_per_drawing_unit: units, method }),
    onSuccess: onDone,
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Could not confirm scale."),
  });

  // Plain decimal only — the backend parses units_per_drawing_unit as a
  // decimal string > 0 (no scientific notation, no negatives).
  const valid = /^\d+(\.\d+)?$/.test(units.trim()) && Number(units) > 0;

  return (
    <div className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 p-3">
      <p className="text-xs font-medium text-ink-800">
        Confirm scale (required before measurement)
      </p>
      <p className="mb-2 mt-0.5 text-[11px] text-ink-500">
        Real-world units per drawing unit. The system never guesses this — you
        confirm it, and only then can runs measure.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor={`sc-${sheet.id}`}>
            Units per drawing unit
          </label>
          <input
            id={`sc-${sheet.id}`}
            className="input !w-36 !py-1.5 !text-xs"
            placeholder="e.g. 0.001"
            inputMode="decimal"
            value={units}
            onChange={(e) => setUnits(e.target.value)}
          />
        </div>
        <div>
          <label className="label !mb-0.5 !text-[11px]" htmlFor={`sm-${sheet.id}`}>
            Method
          </label>
          <select
            id={`sm-${sheet.id}`}
            className="input !w-44 !py-1.5 !text-xs"
            value={method}
            onChange={(e) => setMethod(e.target.value as ScaleMethod)}
          >
            <option value="user_two_point">Two-point calibration</option>
            <option value="user_known_ratio">Known ratio</option>
          </select>
        </div>
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 !text-xs"
          disabled={!valid || confirm.isPending}
          onClick={() => confirm.mutate()}
        >
          {confirm.isPending ? "Confirming…" : "Confirm scale"}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}
    </div>
  );
}

/** One drawing row: header + expandable sheet list. */
function DrawingRow({ drawing }: { drawing: DrawingListItem }) {
  const [open, setOpen] = useState(drawing.parse_status === "parsed");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const qc = useQueryClient();
  // useSheets caches by drawing id; enabled only while expanded.
  const detail = useSheets(open ? drawing.id : null);

  const warnings = drawing.parse_warnings ?? [];

  useEffect(() => {
    if (!open || drawing.format !== "raster" || drawing.parse_status !== "parsed") {
      setPreviewUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setPreviewError(null);
    void api
      .getDrawingPreview(drawing.id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setPreviewUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPreviewError(err instanceof ApiError ? err.message : "Could not load preview.");
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setPreviewUrl(null);
    };
  }, [drawing.format, drawing.id, drawing.parse_status, open]);

  return (
    <div className="card p-4">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-ink-900">
            {drawing.filename}
          </span>
          <span className="shrink-0 rounded bg-ink-100 px-1.5 py-0.5 text-[11px] font-medium uppercase text-ink-600">
            {drawing.format}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {warnings.length > 0 ? (
            <span className="text-[11px] text-amber-700">{warnings.length} warnings</span>
          ) : null}
          <StatusBadge badge={parseStatusBadge(drawing.parse_status)} />
          <span className="text-xs text-ink-400">{open ? "Hide" : "Sheets"}</span>
        </span>
      </button>

      {drawing.parse_status === "failed" && warnings.length > 0 ? (
        <ul className="mt-2 space-y-1 rounded-md bg-red-50 p-2 text-xs text-red-700">
          {warnings.map((w, i) => (
            <li key={i}>• {w}</li>
          ))}
        </ul>
      ) : null}

      {open ? (
        drawing.format === "raster" ? (
          <div className="mt-3 space-y-2">
            <div className="rounded-md border border-amber-200 bg-amber-50/60 p-3 text-xs text-ink-700">
              <p className="font-medium text-ink-900">Raster review surface</p>
              <p className="mt-1">
                Pixels are shown for human review only. Scale is unknown and no
                deterministic quantities or geometry are inferred from this image.
              </p>
            </div>
            {previewError ? (
              <p className="text-xs text-red-700">{previewError}</p>
            ) : previewUrl ? (
              <img
                src={previewUrl}
                alt={`${drawing.filename} raster preview`}
                className="max-h-[520px] w-full rounded-md border border-ink-200 bg-ink-50 object-contain"
              />
            ) : (
              <div className="h-40 animate-pulse rounded bg-ink-100" />
            )}
          </div>
        ) : detail.isPending ? (
          <div className="mt-3 h-20 animate-pulse rounded bg-ink-100" />
        ) : detail.isError ? (
          <p className="mt-3 text-xs text-red-700">
            {detail.error instanceof ApiError
              ? detail.error.message
              : "Could not load sheets."}
          </p>
        ) : detail.data && detail.data.sheets.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {detail.data.sheets.map((s) => (
              <li key={s.id} className="rounded-md border border-ink-200 p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-ink-800">
                    {s.sheet_ref}
                  </span>
                  {s.title ? (
                    <span className="text-xs text-ink-500">{s.title}</span>
                  ) : null}
                  {s.is_modelspace ? (
                    <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700">
                      modelspace
                    </span>
                  ) : null}
                  <StatusBadge
                    badge={calibrationBadge(s.calibration?.status ?? "unknown")}
                  />
                </div>
                {s.calibration?.status !== "confirmed" ? (
                  <ScaleConfirmForm
                    sheet={s}
                    onDone={() => {
                      // Invalidate BOTH the single-drawing key and the
                      // drawings prefix — the Runs tab composites its
                      // sheet options under ["drawings", "details", ...],
                      // which only a prefix invalidation reaches.
                      qc.invalidateQueries({ queryKey: ["drawing", drawing.id] });
                      qc.invalidateQueries({ queryKey: ["drawings"] });
                    }}
                  />
                ) : (
                  <p className="mt-1.5 text-[11px] text-ink-500">
                    Scale confirmed by you: {s.calibration?.units_per_drawing_unit} units per
                    drawing unit ({s.calibration?.method})
                  </p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-xs text-ink-500">
            No sheets parsed from this drawing yet.
          </p>
        )
      ) : null}
    </div>
  );
}

export default function DrawingsTab({ projectId }: { projectId: string }) {
  const drawings = useQuery({
    queryKey: ["drawings", projectId],
    queryFn: () => api.listDrawings(projectId),
  });

  return (
    <div className="space-y-4">
      <UploadCard projectId={projectId} />
      {drawings.isPending ? (
        <div className="card h-24 animate-pulse bg-ink-100" />
      ) : drawings.isError ? (
        <div className="card p-6 text-center">
          <p className="mb-2 text-sm text-red-700">
            {drawings.error instanceof ApiError
              ? drawings.error.message
              : "Could not load drawings."}
          </p>
          <button className="btn-secondary" onClick={() => drawings.refetch()}>
            Retry
          </button>
        </div>
      ) : drawings.data && drawings.data.items.length > 0 ? (
        <div className="space-y-3">
          {drawings.data.items.map((d: DrawingListItem) => (
            <DrawingRow key={d.id} drawing={d} />
          ))}
        </div>
      ) : (
        <div className="card grid place-items-center p-10 text-center">
          <p className="text-sm font-medium text-ink-700">No drawings yet</p>
          <p className="mt-1 text-xs text-ink-500">
            Upload a DXF or PDF to begin. Sheets must have confirmed scale before any
            measurement runs.
          </p>
        </div>
      )}
    </div>
  );
}
