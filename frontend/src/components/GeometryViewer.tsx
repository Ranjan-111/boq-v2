import { useEffect, useMemo, useRef, useState } from "react";
import { pointsAttribute, viewBoxFromBBox, computeBBox, zoomAtPoint } from "../lib/geometry";

/** One drawable geometry: a raw evidence geometry tagged with a group id. */
export interface ViewerGeometry {
  /** Group key (e.g. `${measurementId}#${index}`). */
  id: string;
  geom_type: "line" | "polyline" | "polygon";
  coordinates: number[][];
  layer: string | null;
  /** Highlighted (selected measurement) — red instead of blue. */
  highlighted: boolean;
}

export interface GeometryViewerProps {
  /** All loaded geometries; highlighted ones render red. */
  geometries: ViewerGeometry[];
  /** Optional dashed centerlines (drawing coords, y-up). */
  centerlines?: number[][][];
  /** Shown when there is nothing to render yet. */
  emptyHint?: string;
}

const BLUE_STROKE = "#2563eb";
const BLUE_FILL = "rgba(37,99,235,0.08)";
const RED_STROKE = "#dc2626";
const RED_FILL = "rgba(220,38,38,0.12)";

interface Transform {
  tx: number;
  ty: number;
  scale: number;
}

const IDENTITY: Transform = { tx: 0, ty: 0, scale: 1 };

/**
 * First-slice geometry viewer (T112): normalized-geometry SVG overlay with
 * pan (pointer drag), wheel zoom (0.2–8, cursor-anchored) and a Fit button.
 * Drawing Y is flipped around the bbox center so modelspace coordinates
 * (y-up) render right-side up.
 */
export default function GeometryViewer({
  geometries,
  centerlines = [],
  emptyHint = "Select a measurement row to load its evidence geometry.",
}: GeometryViewerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [t, setT] = useState<Transform>(IDENTITY);
  const drag = useRef<{ px: number; py: number } | null>(null);

  const bbox = useMemo(
    () => computeBBox([...geometries.map((g) => g.coordinates), ...centerlines]),
    [geometries, centerlines],
  );
  const viewBox = useMemo(() => (bbox ? viewBoxFromBBox(bbox) : null), [bbox]);
  // y_draw -> (minY+maxY) - y_draw keeps the content inside the viewBox.
  const flipOffset = bbox ? bbox.minY + bbox.maxY : 0;

  // Wheel zoom must be non-passive to preventDefault page scroll.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !viewBox) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      // Client position in viewBox units.
      const origin = {
        x: viewBox.x + ((e.clientX - rect.left) / rect.width) * viewBox.width,
        y: viewBox.y + ((e.clientY - rect.top) / rect.height) * viewBox.height,
      };
      setT((cur) => zoomAtPoint(cur, e.deltaY < 0 ? 1.15 : 1 / 1.15, origin));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [viewBox]);

  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    drag.current = { px: e.clientX, py: e.clientY };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag.current || !viewBox) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    // Screen px -> viewBox units.
    const dx = ((e.clientX - drag.current.px) / rect.width) * viewBox.width;
    const dy = ((e.clientY - drag.current.py) / rect.height) * viewBox.height;
    drag.current = { px: e.clientX, py: e.clientY };
    setT((cur) => ({ ...cur, tx: cur.tx + dx, ty: cur.ty + dy }));
  }
  function onPointerUp() {
    drag.current = null;
  }

  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink-900">Geometry evidence</h3>
        <button
          type="button"
          className="btn-secondary !px-2 !py-0.5 !text-xs"
          onClick={() => setT(IDENTITY)}
        >
          Fit
        </button>
      </div>
      {viewBox ? (
        <svg
          ref={svgRef}
          viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
          preserveAspectRatio="xMidYMid meet"
          className="h-[420px] w-full cursor-grab touch-none select-none rounded-md bg-ink-50"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        >
          <g
            transform={`translate(${t.tx} ${t.ty}) scale(${t.scale}) translate(0 ${flipOffset}) scale(1 -1)`}
          >
            {geometries.map((g) => {
              const stroke = g.highlighted ? RED_STROKE : BLUE_STROKE;
              const fill = g.highlighted ? RED_FILL : BLUE_FILL;
              const pts = pointsAttribute(g.coordinates);
              return g.geom_type === "polygon" ? (
                <polygon key={g.id} points={pts} fill={fill} stroke={stroke} strokeWidth={1.5 / t.scale} />
              ) : (
                <polyline key={g.id} points={pts} fill="none" stroke={stroke} strokeWidth={1.5 / t.scale} />
              );
            })}
            {centerlines.map((ring, i) => (
              <polyline
                key={`cl-${i}`}
                points={pointsAttribute(ring)}
                fill="none"
                stroke="#0f172a"
                strokeWidth={1 / t.scale}
                strokeDasharray={`${6 / t.scale} ${4 / t.scale}`}
              />
            ))}
          </g>
        </svg>
      ) : (
        <div className="grid h-40 place-items-center rounded-md bg-ink-50 text-xs text-ink-500">
          {emptyHint}
        </div>
      )}
      <p className="mt-2 text-[11px] text-ink-400">
        Drag to pan · scroll to zoom (0.2×–8×) · Fit resets the view. Red geometry belongs to the selected
        measurement.
      </p>
    </div>
  );
}
