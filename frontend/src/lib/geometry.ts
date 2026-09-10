/**
 * Viewer geometry math — pure functions (unit-tested in
 * tests/geometry.test.ts). No DOM, no React.
 */

export interface BBox {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface ViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function isFinitePoint(pt: number[]): pt is number[] {
  return (
    pt.length >= 2 &&
    Number.isFinite(pt[0]) &&
    Number.isFinite(pt[1])
  );
}

/** Bounding box across all coordinate arrays; null if no finite points. */
export function computeBBox(coords: number[][][]): BBox | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let seen = false;
  for (const ring of coords) {
    for (const pt of ring) {
      if (!isFinitePoint(pt)) continue;
      const [x, y] = pt;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      seen = true;
    }
  }
  return seen ? { minX, minY, maxX, maxY } : null;
}

/**
 * SVG viewBox from a bbox with symmetric padding (fraction of the larger
 * dimension). Handles the degenerate (point/line) case where width/height is
 * 0 by falling back to padding on both axes. Y is NOT flipped here — the
 * viewer component flips via a transform so drawing coords stay untouched.
 */
export function viewBoxFromBBox(bbox: BBox, padFraction = 0.05): ViewBox {
  const w = bbox.maxX - bbox.minX;
  const h = bbox.maxY - bbox.minY;
  const span = Math.max(w, h);
  const pad = Math.max(span, 1) * padFraction;
  return {
    x: bbox.minX - pad,
    y: bbox.minY - pad,
    width: Math.max(w + 2 * pad, 2 * pad),
    height: Math.max(h + 2 * pad, 2 * pad),
  };
}

/** Convert a geometry list (raw evidence shapes) into polyline rings. */
export function viewBoxFromGeometries(
  geometries: { geom_type: string; coordinates: number[][] }[],
  padFraction = 0.05,
): ViewBox | null {
  const bbox = computeBBox(geometries.map((g) => g.coordinates));
  return bbox ? viewBoxFromBBox(bbox, padFraction) : null;
}

/**
 * Clamp a zoom scale between min and max.
 */
export function clampScale(scale: number, min = 0.2, max = 8): number {
  return Math.min(max, Math.max(min, scale));
}

/** Polyline/polygon points as an SVG `points` attribute string. */
export function pointsAttribute(ring: number[][]): string {
  return ring
    .filter(isFinitePoint)
    .map(([x, y]) => `${x},${y}`)
    .join(" ");
}

/** Pan/zoom state as used by the viewer component. */
export interface PanZoom {
  scale: number;
  tx: number;
  ty: number;
}

/**
 * Wheel zoom: a new scale, keeping the point under the cursor fixed.
 * `origin` is in viewBox units. Pure math, DOM-free — tested.
 */
export function zoomAtPoint(
  current: PanZoom,
  factor: number,
  origin: { x: number; y: number },
  min = 0.2,
  max = 8,
): PanZoom {
  const newScale = clampScale(current.scale * factor, min, max);
  // Keep the viewBox point under the cursor stationary: solve the translate
  // that maps origin (in viewBox units) to itself under the new scale.
  const k = newScale / current.scale;
  return {
    scale: newScale,
    tx: origin.x - k * (origin.x - current.tx),
    ty: origin.y - k * (origin.y - current.ty),
  };
}
