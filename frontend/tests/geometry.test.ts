import { describe, expect, it } from "vitest";
import {
  clampScale,
  computeBBox,
  pointsAttribute,
  viewBoxFromBBox,
  viewBoxFromGeometries,
  zoomAtPoint,
} from "../src/lib/geometry";

describe("computeBBox", () => {
  it("computes min/max over all points including negatives", () => {
    const bbox = computeBBox([
      [
        [-10, -5],
        [10, 5],
      ],
      [[15, -20]],
    ]);
    expect(bbox).toEqual({ minX: -10, minY: -20, maxX: 15, maxY: 5 });
  });

  it("returns null for empty input", () => {
    expect(computeBBox([])).toBeNull();
    expect(computeBBox([[]])).toBeNull();
  });

  it("ignores non-finite points", () => {
    const bbox = computeBBox([
      [
        [0, 0],
        [Number.NaN, 3],
        [4, Number.POSITIVE_INFINITY],
        [2, 2],
      ],
    ]);
    expect(bbox).toEqual({ minX: 0, minY: 0, maxX: 2, maxY: 2 });
  });
});

describe("viewBoxFromBBox", () => {
  it("adds symmetric padding as a fraction of the larger dimension", () => {
    // bbox 100 wide, 50 tall -> pad = 5
    const vb = viewBoxFromBBox({ minX: 0, minY: 0, maxX: 100, maxY: 50 }, 0.05);
    expect(vb).toEqual({ x: -5, y: -5, width: 110, height: 60 });
  });

  it("pads both axes on a degenerate (single point) bbox", () => {
    const vb = viewBoxFromBBox({ minX: 3, minY: -7, maxX: 3, maxY: -7 });
    // span=0 -> max(span,1)=1 -> pad 0.05, min extent 0.1
    expect(vb.width).toBeCloseTo(0.1);
    expect(vb.height).toBeCloseTo(0.1);
    expect(vb.x).toBeCloseTo(2.95);
    expect(vb.y).toBeCloseTo(-7.05);
  });
});

describe("viewBoxFromGeometries", () => {
  it("handles mixed line/polygon geometries with negative coordinates", () => {
    const vb = viewBoxFromGeometries(
      [
        {
          geom_type: "line",
          coordinates: [
            [-10, -5],
            [10, 5],
          ],
        },
        {
          geom_type: "polygon",
          coordinates: [
            [0, 0],
            [20, 0],
            [20, -30],
            [0, -30],
          ],
        },
      ],
      0.1,
    );
    expect(vb).not.toBeNull();
    // span: w=30, h=35 -> pad = 3.5 (fraction of the larger dimension)
    expect(vb!.x).toBe(-13.5); // minX -10 - 3.5
    expect(vb!.width).toBe(37); // 30 span + 7 pad
    expect(vb!.y).toBe(-33.5); // minY -30 - 3.5
    expect(vb!.height).toBe(42); // 35 span + 7 pad
  });

  it("returns null when no geometries", () => {
    expect(viewBoxFromGeometries([])).toBeNull();
  });
});

describe("clampScale", () => {
  it("clamps to [0.2, 8]", () => {
    expect(clampScale(0.05)).toBe(0.2);
    expect(clampScale(100)).toBe(8);
    expect(clampScale(1)).toBe(1);
  });
});

describe("zoomAtPoint", () => {
  it("keeps the point under the cursor stationary", () => {
    const origin = { x: 50, y: 50 };
    const cur = { scale: 1, tx: 10, ty: 10 };
    const next = zoomAtPoint(cur, 2, origin);
    expect(next.scale).toBe(2);
    // Content point under the cursor: (origin - translate) / scale stays 40.
    expect((origin.x - next.tx) / next.scale).toBe(40);
    expect(next.tx).toBe(-30);
    expect(next.ty).toBe(-30);
  });

  it("clamps at the max scale and stays consistent", () => {
    const next = zoomAtPoint({ scale: 7, tx: 0, ty: 0 }, 2, { x: 100, y: 100 });
    expect(next.scale).toBe(8);
  });

  it("clamps at the min scale", () => {
    const next = zoomAtPoint({ scale: 0.3, tx: 0, ty: 0 }, 0.1, { x: 0, y: 0 });
    expect(next.scale).toBe(0.2);
  });
});

describe("pointsAttribute", () => {
  it("formats coordinate pairs for SVG", () => {
    expect(
      pointsAttribute([
        [1, 2],
        [3, 4],
      ]),
    ).toBe("1,2 3,4");
  });

  it("drops non-finite points", () => {
    expect(pointsAttribute([[0, 0], [Number.NaN, 1], [2, 2]])).toBe("0,0 2,2");
  });
});
