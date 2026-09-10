"""Deduction rules (T046) — openings subtracted from wall quantities.

docs/domain-model.md §Measurement states: MEASURED_ZERO is "computed exactly
0 (e.g., opening deduction to zero) — INFO-flagged, not silent". This module
implements the deterministic deduction math as pure functions; the engine
binds them into rules with full provenance.

Doctrine (never a guess):
  * the deduction area per opening comes from takeoff.openings (bbox-derived
    for named blocks — the drawn geometry IS the evidence — or the gap
    extent for face gaps), clamped to the wall's own thickness: a deduction
    can never remove more wall than the wall contains,
  * a wall with no openings has zero deduction — MEASURED_ZERO with the
    wall's own handles as evidence (never silently omitted: an estimator
    opening the row sees "0 openings; nothing deducted"),
  * total deduction exceeding the wall area is refused (negative areas are
    a modeling error, not a quantity),
  * all arithmetic is float64 over drawing units; project-unit conversion
    happens only in core.units (human-gated), never here.
"""
from __future__ import annotations

from dataclasses import dataclass

from takeoff.kernel import area_of
from takeoff.openings import OpeningRecord
from takeoff.wall_detection import WallCandidate, wall_footprint


class DeductionError(ValueError):
    """The deduction cannot be computed honestly (never a guess)."""


@dataclass(frozen=True, slots=True)
class WallDeduction:
    """Deterministic deduction result for one wall."""

    wall_index: int
    gross_area: float  # wall footprint area, drawing units squared
    opening_count: int
    opening_area: float  # total deductible opening area in this wall
    net_area: float  # gross - opening_area (>= 0 always)
    opening_handles: tuple[str, ...]  # entity refs of the contributing openings


def wall_deductions(
    walls: list[WallCandidate], openings: list[OpeningRecord]
) -> list[WallDeduction]:
    """Deduct opening areas from wall footprint areas, per wall.

    Openings are attributed by wall_index (the openings detector already
    resolved the wall each opening belongs to; ambiguous ones never arrive
    here). Deterministic: walls in order, openings grouped by index.
    """
    per_wall: dict[int, list[OpeningRecord]] = {}
    for o in openings:
        per_wall.setdefault(o.wall_index, []).append(o)
    out: list[WallDeduction] = []
    for wi, wall in enumerate(walls):
        gross = area_of(wall_footprint(wall))
        wall_openings = sorted(
            per_wall.get(wi, ()), key=lambda o: (o.center_distance, o.method)
        )
        opening_area = 0.0
        handles: list[str] = []
        for o in wall_openings:
            # The per-opening deduction is already thickness-clamped by the
            # openings detector (OpeningRecord.area); sum deterministically.
            opening_area += o.area
            handles.extend(h.entity_ref for h in o.source_handles)
        net = gross - opening_area
        if net < -1e-6:
            raise DeductionError(
                f"wall {wi}: openings ({opening_area}) exceed footprint ({gross})"
            )
        if net < 0:  # float noise exactly at zero
            net = 0.0
        out.append(WallDeduction(
            wall_index=wi,
            gross_area=gross,
            opening_count=len(wall_openings),
            opening_area=opening_area,
            net_area=net,
            opening_handles=tuple(sorted(set(handles))),
        ))
    return out


def floor_totals(
    room_gross_areas: list[float],
    room_net_areas: list[float],
) -> dict[str, float]:
    """T044 floor roll-up: per-room aggregation to floor totals.

    Gross floor area = sum of room gross areas; net = sum of room nets.
    A floor with zero rooms is an honest zero, not an exception (a plan may
    legitimately show no enclosed rooms yet).
    """
    if any(a < -1e-9 for a in (*room_gross_areas, *room_net_areas)):
        raise DeductionError("negative room area cannot total a floor")
    return {
        "gross": sum(max(0.0, a) for a in room_gross_areas),
        "net": sum(max(0.0, a) for a in room_net_areas),
        "room_count": float(len(room_gross_areas)),
    }
