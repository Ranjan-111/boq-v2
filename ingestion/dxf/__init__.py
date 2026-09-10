"""DXF parser (T030/T031) — ezdxf → normalized geometry + stable handles.

The provenance differentiator (reuse-matrix #7): every normalized geometry
carries the entity's dxf.handle — stable across re-parses — so any quantity
can be traced back to the exact source entity. Positional ids are never used.

Behavioral stance (pattern-inspired by OCErp's honest-unit handling, code
written fresh against docs/domain-model.md):
  * $INSUNITS header is read, NEVER guessed; missing → unit "unknown" and the
    sheet cannot be measured until a human confirms scale.
  * Modelspace is the measurable sheet for V1; paper-space layouts are parsed
    but not measurable (refuse to guess which viewport is the drawing).
  * Block references (INSERT) are exploded into their placements as separate
    geometry, keeping the INSERT's handle + the block entity's handle.
"""
from __future__ import annotations

import hashlib
import io
import math
from typing import Any

import ezdxf
from ezdxf.entities import DXFEntity  # type: ignore[attr-defined]
from ezdxf.xclip import XClip

from core.domain.enums import GeomType, SourceFormat
from core.geometry import (
    NormalizedGeometry,
    ParseResult,
    SheetSummary,
    SourceHandleRef,
)

# $INSUNITS → our unit vocabulary (docs/domain-model.md: never guessed).
_INSUNITS_CODES: dict[int, str] = {
    1: "in",
    2: "ft",
    4: "mm",
    5: "cm",
    6: "m",
}

# Entity types we normalize. Anything else is skipped with a warning (never
# silently dropped, never guessed at).
_MEASURABLE_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE"})


class DxfParseError(ValueError):
    """Structural DXF problems that make parsing impossible."""


def _handle_of(entity: DXFEntity) -> str:
    """Stable identity: the entity's dxf.handle (e.g. '2A1F')."""
    # ezdxf's placed virtual entities have no handle of their own. Their
    # origin points back to the actual block-definition member in the file.
    source = entity.origin_of_copy or entity
    handle = source.dxf.handle
    if handle in (None, "", "0"):
        raise DxfParseError("entity has no stable source handle")
    return str(handle)


def _handle_ref(sheet_ref: str, entity: DXFEntity) -> SourceHandleRef:
    layer = str(entity.dxf.layer) if entity.dxf.hasattr("layer") else None
    return SourceHandleRef(
        format=SourceFormat.DXF_ENTITY,
        sheet_ref=sheet_ref,
        entity_ref=_handle_of(entity),
        layer=layer,
    )


def _vertices_of(entity: DXFEntity) -> list[tuple[float, float]]:
    """Read preflight-validated WCS XY straight vertices without reinterpretation."""
    etype = entity.dxftype()
    if etype == "LINE":
        s = entity.dxf.start
        e = entity.dxf.end
        return [(s.x, s.y), (e.x, e.y)]
    if etype == "LWPOLYLINE":
        pts = list(entity.get_points("xy"))  # type: ignore[attr-defined]
        if entity.closed and pts:  # type: ignore[attr-defined]
            return [*pts, pts[0]]
        return pts
    if etype == "POLYLINE":
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]  # type: ignore[attr-defined]
        if entity.is_closed and pts:  # type: ignore[attr-defined]
            return [*pts, pts[0]]
        return pts
    raise DxfParseError(f"no vertices for {etype}")


def _raw_vertices_of(entity: DXFEntity) -> list[tuple[float, float]]:
    """Vertices EXCLUDING the closure point — the honest vertex count.

    `_vertices_of` appends the closing point for closed polylines, which
    would let a closed 1-vertex polyline pass a `len < 2` preflight as a
    phantom second point. Vertex-count and degeneracy checks must count
    real, explicitly-drawn vertices only.
    """
    etype = entity.dxftype()
    if etype == "LINE":
        return _vertices_of(entity)
    if etype == "LWPOLYLINE":
        return list(entity.get_points("xy"))  # type: ignore[attr-defined]
    if etype == "POLYLINE":
        return [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]  # type: ignore[attr-defined]
    raise DxfParseError(f"no vertices for {etype}")


def _normalize_entity(
    entity: DXFEntity,
    sheet_ref: str,
    insertion: tuple[float, float] = (0.0, 0.0),
    block_handles: tuple[SourceHandleRef, ...] = (),
) -> NormalizedGeometry | None:
    """One entity → one NormalizedGeometry (or None if not measurable).

    INSERT members carry BOTH the INSERT's handle and their own (full chain).
    """
    etype = entity.dxftype()
    handles = (*block_handles, _handle_ref(sheet_ref, entity))
    layer = str(entity.dxf.layer) if entity.dxf.hasattr("layer") else None

    def shift(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] + insertion[0], p[1] + insertion[1])

    if etype in ("LINE", "LWPOLYLINE", "POLYLINE"):
        pts = [shift(p) for p in _vertices_of(entity)]
        if len(pts) < 2:
            return None
        closed = len(pts) >= 3 and pts[0] == pts[-1]
        geom_type = GeomType.POLYGON if closed else GeomType.POLYLINE
        return NormalizedGeometry(
            geom_type=geom_type,
            coordinates=pts,
            source_format=SourceFormat.DXF_ENTITY,
            source_handles=handles,
            layer=layer,
        )
    return None


def _unsupported_reason(entity: Any) -> str | None:
    """V1 supports finite straight zero-width geometry in the WCS XY plane.

    Refuse curves and 3D/OCS semantics; never flatten them into source lines.
    This preflight runs on block definitions AND their placed virtual copies.
    """
    etype = entity.dxftype()
    if etype not in _MEASURABLE_TYPES | {"INSERT"}:
        return "entity semantics not supported"
    if tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1):
        return "non-default OCS/extrusion"
    if etype != "INSERT" and entity.dxf.get("thickness", 0) != 0:
        return "nonzero thickness"
    if etype == "INSERT":
        if entity.dxf.insert.z != 0:
            return "nonzero INSERT elevation"
        if entity.dxf.get("row_count", 1) != 1 or entity.dxf.get("column_count", 1) != 1:
            return "MINSERT array"
        if XClip(entity).has_clipping_path:
            return "clipping path (including disabled clipping)"
        if not all(math.isfinite(float(v)) for v in (*entity.dxf.insert,
                entity.dxf.get("rotation", 0), entity.dxf.get("xscale", 1),
                entity.dxf.get("yscale", 1), entity.dxf.get("zscale", 1))):
            return "non-finite INSERT transform"
        return None
    if etype == "LINE":
        if entity.dxf.start.z != 0 or entity.dxf.end.z != 0:
            return "nonzero Z coordinate"
    elif etype == "LWPOLYLINE":
        if entity.dxf.get("elevation", 0) != 0:
            return "nonzero elevation"
        if entity.has_arc:
            return "bulged polyline"
        if entity.has_width:
            return "polyline width"
    else:
        if not entity.is_2d_polyline or entity.dxf.get("flags", 0) & 6:
            return "3D/mesh/fitted POLYLINE"
        if entity.dxf.elevation.z != 0:
            return "nonzero elevation"
        if any(v.dxf.location.z != 0 for v in entity.vertices):
            return "nonzero vertex Z coordinate"
        if any(v.dxf.get("bulge", 0) != 0 for v in entity.vertices):
            return "bulged polyline"
        if (entity.dxf.get("default_start_width", 0)
                or entity.dxf.get("default_end_width", 0) or any(
            v.dxf.get("start_width", 0) or v.dxf.get("end_width", 0) for v in entity.vertices
        )):
            return "polyline width"
    pts = _raw_vertices_of(entity)
    if len(pts) < 2:
        return "fewer than two vertices"
    # A closed ring needs >= 3 distinct vertices to bound an area. Fewer is
    # a degenerate CAD anomaly: normalizing it would invent a phantom
    # polygon (closed 2-point) or a zero-length polyline (closed 1-point)
    # — refused explicitly, never silently reinterpreted.
    closed = etype == "LWPOLYLINE" and entity.closed
    closed = closed or (etype == "POLYLINE" and entity.is_closed)
    if closed and len({tuple(p) for p in pts}) < 3:
        return "closed polyline with fewer than three distinct vertices"
    if not all(math.isfinite(float(v)) for point in pts for v in point):
        return "non-finite coordinates"
    return None


def _warning(entity: DXFEntity, reason: str) -> str:
    return f"unsupported {entity.dxftype()} handle={_handle_of(entity)}: {reason}"


def _explode_insert(entity: Any, sheet_ref: str) -> list[NormalizedGeometry]:
    """Atomic placement: any unsupported/skipped member refuses the whole INSERT."""
    reason = _unsupported_reason(entity)
    if reason:
        raise DxfParseError(reason)
    block = entity.block()
    if block is None:
        raise DxfParseError("missing block definition")
    if block.block.dxf.base_point.z != 0:
        raise DxfParseError("nonzero block base elevation")
    members = list(block)
    if not members:
        raise DxfParseError("empty block definition")
    for member in members:
        reason = "nested INSERT" if member.dxftype() == "INSERT" else _unsupported_reason(member)
        if reason:
            raise DxfParseError(_warning(member, reason))
    skipped: list[str] = []

    def on_skipped(member: DXFEntity, reason: str) -> None:
        skipped.append(_warning(member, reason))

    placed = list(entity.virtual_entities(skipped_entity_callback=on_skipped))
    if skipped or len(placed) != len(members):
        raise DxfParseError("partial/skipped block transform: " + "; ".join(skipped))
    insert_handle = _handle_ref(sheet_ref, entity)
    out: list[NormalizedGeometry] = []
    for member in placed:
        reason = _unsupported_reason(member)
        if reason:
            raise DxfParseError(_warning(member, reason))
        if member.dxf.layer == "0":
            member.dxf.layer = entity.dxf.layer
        geom = _normalize_entity(member, sheet_ref, block_handles=(insert_handle,))
        if geom is None:
            raise DxfParseError(_warning(member, "normalization refused"))
        out.append(geom)
    return out


def _read_units(doc: Any) -> str:
    code = doc.header.get("$INSUNITS", 0)
    return _INSUNITS_CODES.get(int(code or 0), "unknown")


def parse_dxf(data: bytes) -> ParseResult:
    """Parse DXF bytes → ParseResult (geometries + sheets + warnings)."""
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="replace")))  # type: ignore[attr-defined]
    except Exception as exc:
        raise DxfParseError(f"ezdxf could not parse: {exc}") from exc
    if not hasattr(doc, "modelspace"):
        raise DxfParseError("not a DXF drawing")

    msp = doc.modelspace()
    units = _read_units(doc)
    sheet_ref = "modelspace"
    warnings: list[str] = []

    geometries: list[NormalizedGeometry] = []
    measurable = 0
    skipped = 0
    for entity in msp:
        etype = entity.dxftype()
        try:
            if etype == "INSERT":
                geoms = _explode_insert(entity, sheet_ref)
                geometries.extend(geoms)
                measurable += len(geoms)
                continue
            reason = _unsupported_reason(entity)
            if reason:
                raise DxfParseError(reason)
            geom = _normalize_entity(entity, sheet_ref)
            if geom is None:
                raise DxfParseError("normalization refused")
            geometries.append(geom)
            measurable += 1
        except Exception as exc:
            # A malformed placement contributes no partial geometry. The warning
            # is a run blocker, consumed by the parsed-measurement entrypoint.
            skipped += 1
            warnings.append(_warning(entity, str(exc)))

    # Modelspace is the measurable sheet (T031). Paper-space layouts are
    # parsed for completeness but refused: which viewport is "the drawing"?
    sheets = [
        SheetSummary(
            sheet_ref=sheet_ref,
            layout_name="Model",
            entity_count=measurable + skipped,
            measurable_count=measurable,
            is_modelspace=True,
            unit_code=units,
            measurable=measurable > 0 and units != "unknown",
        )
    ]
    for name, layout in _iter_paperspace_layouts(doc):
        count = sum(1 for _ in layout)
        if count == 0:
            continue
        sheets.append(
            SheetSummary(
                sheet_ref=f"paperspace:{name}",
                layout_name=name,
                entity_count=count,
                measurable_count=0,
                is_modelspace=False,
                unit_code=units,
                measurable=False,
            )
        )
        warnings.append(f"paper-space layout {name!r} parsed but not measurable (V1)")

    return ParseResult(
        source_sha256=hashlib.sha256(data).hexdigest(),
        drawing_units=units,
        geometries=tuple(geometries),
        sheets=tuple(sheets),
        warnings=tuple(warnings),
    )


def _iter_paperspace_layouts(doc: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    try:
        names = doc.layouts.names()
    except Exception:
        return out
    for name in names:
        if name == "Model":
            continue
        layout = doc.layouts.get(name)
        if layout is not None:
            out.append((name, layout))
    return out


# ---------------------------------------------------------------------------
# Scale proposal (T034 core): from DXF header only, PROPOSED — never applied.
# ---------------------------------------------------------------------------


def propose_scale_from_units(units: str) -> tuple[str | None, str | None]:
    """PROPOSED calibration candidate from file units: (units_per_drawing_unit, method).

    A DXF drawn in real-world units (INSUNITS=4/5/6) implies 1 drawing unit is
    already physical — the proposal is 1.0 physical-units-per-drawing-unit.
    This NEVER auto-applies; a human confirms via the scale endpoint
    (docs/domain-model.md human gate).
    """
    if units in ("mm", "cm", "m", "in", "ft"):
        return "1.0", "detected_from_dxf_units"
    return None, None


def sheets_of(data: bytes) -> tuple[SheetSummary, ...]:
    """Convenience: parse and return just the sheet summaries."""
    return parse_dxf(data).sheets


__all__ = [
    "DxfParseError",
    "NormalizedGeometry",
    "ParseResult",
    "SheetSummary",
    "SourceHandleRef",
    "parse_dxf",
    "propose_scale_from_units",
    "sheets_of",
]
