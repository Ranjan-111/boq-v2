"""Measurement rules registry (T041) — rule_id, versioned, replayable.

docs/domain-model.md §Measurement: `computation` replayable as
{rule_id, engine_version, inputs_digest} — same inputs + rule = same value,
verified by the determinism tests (T050).

A rule is a pure function from named inputs to a quantity in DRAWING UNITS
plus the source handles of every input geometry. It NEVER converts units or
applies scale (that is core.units' job, human-gated) and never writes AI
fields.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from core.geometry import NormalizedGeometry

# Round 5: rooms/floors/openings/deductions join the measured vocabulary;
# every new rule version-stamps its own replay, and the engine version bumps
# because the set of rules a run may emit changed.
ENGINE_VERSION = "0.4.0"


class RuleFn(Protocol):
    def __call__(self, inputs: list[NormalizedGeometry]) -> float: ...


@dataclass(frozen=True, slots=True)
class Rule:
    """One deterministic measurement rule, registered with a stable id."""

    rule_id: str
    version: str
    quantity_type: str  # length | area | count
    description: str
    fn: RuleFn


_REGISTRY: dict[str, Rule] = {}


def register(
    rule_id: str,
    *,
    quantity_type: str,
    description: str,
    version: str = ENGINE_VERSION,
) -> Callable[[RuleFn], RuleFn]:
    """Decorator: register a rule. Duplicate ids are a programming error."""

    def deco(fn: RuleFn) -> RuleFn:
        if rule_id in _REGISTRY:
            raise ValueError(f"duplicate rule id: {rule_id}")
        _REGISTRY[rule_id] = Rule(
            rule_id=rule_id,
            version=version,
            quantity_type=quantity_type,
            description=description,
            fn=fn,
        )
        return fn

    return deco


def get_rule(rule_id: str) -> Rule:
    try:
        return _REGISTRY[rule_id]
    except KeyError:
        raise ValueError(f"unknown rule: {rule_id}") from None


def all_rules() -> list[Rule]:
    return sorted(_REGISTRY.values(), key=lambda r: r.rule_id)


def run_rule(rule_id: str, inputs: list[NormalizedGeometry]) -> float:
    """Execute a rule deterministically. Raises the kernel's NotMeasurable."""
    rule = get_rule(rule_id)
    return rule.fn(inputs)


# ---------------------------------------------------------------------------
# Built-in rules (V1 vertical slice)
# ---------------------------------------------------------------------------


@register(
    "wall.centerline.length.v1",
    quantity_type="length",
    description="Centerline length of one detected wall (drawing units)",
)
def _wall_centerline_length(inputs: list[NormalizedGeometry]) -> float:
    """Wall centerline length from its two face geometries.

    inputs: exactly the two edge geometries of one WallCandidate. The centerline
    is the midpoint path of the offset pair: its length equals each face's
    length for straight parallel pairs (they are congruent by construction).
    """
    from takeoff.wall_detection import are_offset_pair, extract_segs

    if len(inputs) != 2:
        raise ValueError("wall.centerline.length.v1 takes exactly the two faces")
    segs = extract_segs(inputs, wall_layers_only=False)
    if len(segs) != 2 or not are_offset_pair(segs[0], segs[1]):
        raise ValueError("wall faces require congruent longitudinal support")
    return segs[0].length


@register(
    "wall.footprint.area.v1",
    quantity_type="area",
    description="Footprint area of one detected wall (drawing units squared)",
)
def _wall_footprint_area(inputs: list[NormalizedGeometry]) -> float:
    """Footprint area = centerline length x thickness (drawing units squared)."""
    if len(inputs) != 1:
        raise ValueError("wall.footprint.area.v1 takes the footprint geometry")
    from takeoff.kernel import area_of

    return area_of(inputs[0])


@register(
    "polyline.length.v1",
    quantity_type="length",
    description="Polyline/line length (drawing units)",
)
def _polyline_length(inputs: list[NormalizedGeometry]) -> float:
    from takeoff.kernel import length_of

    if len(inputs) != 1:
        raise ValueError("polyline.length.v1 takes exactly one geometry")
    return length_of(inputs[0])


@register(
    "polygon.area.v1",
    quantity_type="area",
    description="Polygon area (drawing units squared)",
)
def _polygon_area(inputs: list[NormalizedGeometry]) -> float:
    from takeoff.kernel import area_of

    if len(inputs) != 1:
        raise ValueError("polygon.area.v1 takes exactly one geometry")
    return area_of(inputs[0])


@register("count.v1", quantity_type="count", description="Count of geometries")
def _count(inputs: list[NormalizedGeometry]) -> float:
    return float(len(inputs))


# ---------------------------------------------------------------------------
# Round 5 rules — rooms, floors, openings, deductions (T043-T046)
# ---------------------------------------------------------------------------


@register(
    "room.gross.area.v1",
    quantity_type="area",
    description="Gross room area to wall centerlines (drawing units squared)",
)
def _room_gross_area(inputs: list[NormalizedGeometry]) -> float:
    """Room gross area: the centerline-bounded region of one enclosed room."""
    if len(inputs) != 1:
        raise ValueError("room.gross.area.v1 takes exactly one room ring geometry")
    from takeoff.kernel import area_of

    return area_of(inputs[0])


@register(
    "room.net.area.v1",
    quantity_type="area",
    description="Net room area (gross minus wall footprints, drawing units squared)",
)
def _room_net_area(inputs: list[NormalizedGeometry]) -> float:
    """Net room area: the clear interior region of one enclosed room."""
    if len(inputs) != 1:
        raise ValueError("room.net.area.v1 takes exactly one net-ring geometry")
    from takeoff.kernel import area_of

    return area_of(inputs[0])


@register(
    "floor.gross.area.v1",
    quantity_type="area",
    description="Floor gross area (sum of room gross areas on one storey)",
)
def _floor_gross_area(inputs: list[NormalizedGeometry]) -> float:
    """Sum of room gross rings — the floor roll-up rule (T044)."""
    if not inputs:
        raise ValueError("floor.gross.area.v1 takes at least one room ring")
    from takeoff.kernel import area_of

    return float(sum(area_of(g) for g in inputs))


@register(
    "floor.net.area.v1",
    quantity_type="area",
    description="Floor net area (sum of room net rings on one storey)",
)
def _floor_net_area(inputs: list[NormalizedGeometry]) -> float:
    """Sum of room net rings — the floor roll-up rule (T044)."""
    if not inputs:
        raise ValueError("floor.net.area.v1 takes at least one net ring")
    from takeoff.kernel import area_of

    return float(sum(area_of(g) for g in inputs))


@register(
    "wall.net.area.v1",
    quantity_type="area",
    description="Wall footprint area net of opening deductions (drawing units squared)",
)
def _wall_net_area(inputs: list[NormalizedGeometry]) -> float:
    """Net wall area: footprint minus the opening slot geometries.

    inputs[0] = the wall footprint; inputs[1:] = one slot geometry per
    detected opening (takeoff.openings builds them from the drawn geometry).
    The subtraction is GEOMETRIC (shapely difference of the slot union from
    the footprint), so a slot outside the footprint — a face-gap opening,
    whose host wall's drawn faces already exclude the gap — deducts nothing:
    no double-subtraction, no special-casing, fully replayable from geometry.
    """
    if not inputs:
        raise ValueError("wall.net.area.v1 takes the footprint plus opening slots")
    from typing import cast

    from takeoff.kernel import area_of

    net = area_of(inputs[0])
    if len(inputs) > 1:
        from shapely.geometry import Polygon as ShapelyPolygon
        from shapely.ops import unary_union

        fp_coords = cast("list[tuple[float, float]]", inputs[0].coordinates)
        footprint = ShapelyPolygon([(float(x), float(y)) for x, y in fp_coords])
        slots = []
        for s in inputs[1:]:
            s_coords = cast("list[tuple[float, float]]", s.coordinates)
            slots.append(ShapelyPolygon([(float(x), float(y)) for x, y in s_coords]))
        deducted = footprint.intersection(unary_union(slots)).area
        net -= deducted
    if net < -1e-9:
        raise ValueError("opening deductions exceed the wall footprint")
    return max(net, 0.0)


@register(
    "opening.count.v1",
    quantity_type="count",
    description="Count of openings in one wall (one slot geometry per opening)",
)
def _opening_count(inputs: list[NormalizedGeometry]) -> float:
    """Count opening slot geometries — one slot per detected opening (T045).

    Zero openings is an EMPTY input list: an honest 0.0 (MEASURED_ZERO),
    never a count of the wall faces or members.
    """
    return float(len(inputs))
