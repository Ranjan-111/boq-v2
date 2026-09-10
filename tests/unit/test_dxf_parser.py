"""T030/T031 — DXF parser tests: stable handles, INSUNITS, measurability.

The provenance contract under test:
  * every normalized geometry carries >=1 DXF handle (dxf.handle, stable),
  * INSUNITS is read, never guessed (absent → "unknown" → not measurable),
  * modelspace is the measurable sheet; paper space is refused,
  * corrupt files fail loudly,
  * INSERT explosion keeps the full handle chain,
  * TEXT/MTEXT labels are captured as evidence tokens (T043) — captured
    with their handle or refused with a warning, never silently dropped,
  * opening blocks are classified by name and mapped by INSERT handle (T045).

Unit-marked: pure parsing, no DB. ezdxf is a real dependency, not mocked.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import ezdxf
import pytest

from core.domain.enums import GeomType, SourceFormat
from core.geometry import ParseResult
from ingestion.dxf import (
    DxfParseError,
    block_names_by_insert_handle,
    is_opening_block_name,
    parse_dxf,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dxf"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse(name: str) -> ParseResult:
    return parse_dxf(load(name))


@pytest.mark.unit
class TestStableHandles:
    def test_every_geometry_has_dxf_handle(self) -> None:
        result = parse("wall_plan.dxf")
        assert len(result.geometries) == 4  # 2 walls, each 2 parallel lines
        for geom in result.geometries:
            assert geom.source_handles, "geometry without a source handle"
            for h in geom.source_handles:
                assert h.entity_ref not in ("", "0"), f"bad handle {h!r}"
                assert h.entity_ref is not None

    def test_handles_are_stable_across_reparse(self) -> None:
        r1 = parse("wall_plan.dxf")
        r2 = parse("wall_plan.dxf")
        h1 = [h.entity_ref for g in r1.geometries for h in g.source_handles]
        h2 = [h.entity_ref for g in r2.geometries for h in g.source_handles]
        assert h1 == h2, "handles must be identical across re-parses"

    def test_layer_is_captured(self) -> None:
        result = parse("wall_plan.dxf")
        assert all(g.layer == "WALL" for g in result.geometries)


@pytest.mark.unit
class TestUnitsAndScaleProposal:
    def test_insunits_mm_is_read(self) -> None:
        result = parse("wall_plan.dxf")
        assert result.drawing_units == "mm"
        sheet = result.sheets[0]
        assert sheet.unit_code == "mm"
        assert sheet.measurable is True

    def test_missing_insunits_is_never_guessed(self) -> None:
        result = parse("no_units.dxf")
        assert result.drawing_units == "unknown"
        assert result.sheets[0].measurable is False, "no unit → not measurable"


@pytest.mark.unit
class TestSheets:
    def test_modelspace_is_the_measurable_sheet(self) -> None:
        result = parse("wall_plan.dxf")
        assert result.sheets[0].is_modelspace
        assert result.sheets[0].sheet_ref == "modelspace"

    def test_paperspace_only_is_refused(self) -> None:
        result = parse("paperspace_only.dxf")
        measurable = [s for s in result.sheets if s.measurable]
        assert measurable == [], "paper-space sheet must not be measurable (V1)"
        assert any("not measurable" in w for w in result.warnings)


@pytest.mark.unit
class TestAdversarial:
    def test_corrupt_dxf_raises(self) -> None:
        with pytest.raises(DxfParseError):
            parse("corrupt.dxf")

    def test_empty_bytes_raise(self) -> None:
        with pytest.raises(DxfParseError):
            parse_dxf(b"")

    def test_open_polyline_normalized_as_polyline_not_polygon(self) -> None:
        result = parse("open_polyline.dxf")
        assert result.geometries, "open polyline must still be captured (evidence)"
        assert all(g.geom_type is GeomType.POLYLINE for g in result.geometries)
        assert not any(g.geom_type is GeomType.POLYGON for g in result.geometries)

    def test_closed_bowtie_normalized_as_polygon(self) -> None:
        # Parser level: a closed bowtie IS a polygon; the takeoff kernel refuses it.
        result = parse("self_intersecting.dxf")
        assert any(g.geom_type is GeomType.POLYGON for g in result.geometries)


@pytest.mark.unit
class TestInsertExplosion:
    @pytest.mark.parametrize(
        ("placement", "attributes", "expected"),
        [
            ((100, 200), {}, [(100, 200), (110, 200)]),
            ((100, 200), {"rotation": 90}, [(100, 200), (100, 210)]),
            ((100, 200), {"xscale": -2, "yscale": 3}, [(100, 200), (80, 200)]),
        ],
    )
    def test_placed_member_coordinates_and_source_identity(
        self, placement, attributes, expected
    ) -> None:
        doc = ezdxf.new("R2010")
        doc.units = 4
        block = doc.blocks.new("SEGMENT")
        member = block.add_line((0, 0), (10, 0))
        insert = doc.modelspace().add_blockref("SEGMENT", placement, dxfattribs=attributes)
        stream = io.StringIO()
        doc.write(stream)
        data = stream.getvalue().encode("utf-8")
        result = parse_dxf(data)
        assert len(result.geometries) == 1
        geometry = result.geometries[0]
        for actual, point in zip(geometry.coordinates, expected, strict=True):
            assert actual == pytest.approx(point)
        assert [h.entity_ref for h in geometry.source_handles] == [
            insert.dxf.handle, member.dxf.handle,
        ]
        assert parse_dxf(data).geometries == result.geometries

    def test_block_wall_explodes_with_handle_chain(self) -> None:
        result = parse("block_wall.dxf")
        assert len(result.geometries) == 2, "block has 2 lines"
        for geom in result.geometries:
            # INSERT handle + the block entity's own handle
            assert len(geom.source_handles) == 2
            insert_handles = {h.entity_ref for h in geom.source_handles[:-1]}
            assert insert_handles, "INSERT handle must lead the chain"
        # both geometries belong to the same INSERT
        leading = [g.source_handles[0].entity_ref for g in result.geometries]
        assert leading[0] == leading[1]

    def test_coordinates_shifted_by_insertion(self) -> None:
        result = parse("block_wall.dxf")
        ys = {p[1] for g in result.geometries for p in g.coordinates}
        assert ys == {100.0, -100.0}, "block-local coords must be shifted by insert"


def _parse_document(doc):
    stream = io.StringIO()
    doc.write(stream)
    return parse_dxf(stream.getvalue().encode())


@pytest.mark.unit
@pytest.mark.parametrize("kind", [
    "spline", "ellipse", "point", "hatch", "solid",
    "point_1", "lwpolyline_1",
])
def test_other_entity_types_never_silently_dropped(kind):
    """Vocabulary beyond LINE/LWPOLYLINE/POLYLINE/INSERT is refused WITH a
    warning — the docs contract is "skipped with a warning (never silently
    dropped)". A silent drop would make an unsupported sheet look complete.
    (TEXT/MTEXT are no longer here: they are captured as evidence tokens —
    see TestTextTokens. They stay out of the geometry path entirely.)
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    if kind == "spline":
        entity = msp.add_spline([(0, 0), (5, 5), (10, 0)])
    elif kind == "ellipse":
        entity = msp.add_ellipse((0, 0), (10, 0), 0.5)
    elif kind == "point":
        entity = msp.add_point((3, 3))
    elif kind == "hatch":
        entity = msp.add_hatch()
    elif kind == "solid":
        entity = msp.add_solid([(0, 0), (10, 0), (10, 10), (0, 10)])
    elif kind == "point_1":
        # a degenerate single-point polyline is refused, not reinterpreted
        entity = msp.add_polyline2d([(3, 3)])
    else:
        entity = msp.add_lwpolyline([(3, 3)])
    result = _parse_document(doc)
    assert result.geometries == ()
    assert any(
        entity.dxf.handle in w and entity.dxftype() in w and "unsupported" in w
        for w in result.warnings
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", [
    "lwpolyline_1_closed", "polyline_1_closed", "lwpolyline_2_closed",
    "polyline_2_closed",
])
def test_degenerate_closed_polylines_refused_not_reinterpreted(kind):
    """Degenerate closed polylines — fewer than 3 distinct vertices — cannot
    bound an area and are REFUSED at parse time with a per-handle warning.

    History: the audit first pinned the lenient behavior (closed 1-vertex
    became a zero-length POLYLINE because the preflight counted the appended
    closing point; closed 2-vertex became a degenerate POLYGON whose
    length_of would double-count the span). The doctrine-correct fix
    (raw vertex count + distinct-vertex requirement for closed rings)
    landed in ingestion/dxf/_unsupported_reason; this test now asserts the
    refusal. Flip-back requires a deliberate contract decision.
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    if kind == "lwpolyline_1_closed":
        entity = msp.add_lwpolyline([(3, 3)])
        entity.closed = True
    elif kind == "polyline_1_closed":
        entity = msp.add_polyline2d([(3, 3)])
        entity.dxf.flags = 1  # closed flag survives round-trip
    elif kind == "lwpolyline_2_closed":
        entity = msp.add_lwpolyline([(0, 0), (10, 0)])
        entity.closed = True
    else:
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.dxf.flags = 1
    result = _parse_document(doc)
    assert result.geometries == (), "degenerate closed polyline must not become geometry"
    assert any(
        entity.dxf.handle in w and entity.dxftype() in w and "unsupported" in w
        for w in result.warnings
    )
    # 1-vertex forms are refused as "fewer than two vertices" (raw count);
    # 2-vertex closed forms as "fewer than three distinct vertices".
    assert any(
        "fewer than two vertices" in w or "fewer than three distinct vertices" in w
        for w in result.warnings
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", [
    "circle", "arc", "bulge", "legacy_bulge", "ocs", "elevation", "line3d",
    "polyline3d", "width", "thickness", "legacy_width", "legacy_default_width",
    "legacy_spline_flag", "legacy_elevation", "legacy_vertex_z",
    "nan_coords", "inf_coords",
])
def test_unsupported_semantics_refused_with_source_warning(kind):
    doc = ezdxf.new("R2010")
    doc.units = 4
    msp = doc.modelspace()
    if kind == "circle":
        entity = msp.add_circle((0, 0), 10)
    elif kind == "arc":
        entity = msp.add_arc((0, 0), 10, 0, 90)
    elif kind == "bulge":
        entity = msp.add_lwpolyline([(0, 0, 1), (10, 0, 0)], format="xyb")
    elif kind == "legacy_bulge":
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.vertices[0].dxf.bulge = 1
    elif kind == "ocs":
        entity = msp.add_lwpolyline([(0, 0), (10, 0)], dxfattribs={"extrusion": (0, 1, 0)})
    elif kind == "elevation":
        entity = msp.add_lwpolyline([(0, 0), (10, 0)], dxfattribs={"elevation": 3})
    elif kind == "line3d":
        entity = msp.add_line((0, 0, 1), (10, 0, 2))
    elif kind == "polyline3d":
        entity = msp.add_polyline3d([(0, 0, 0), (10, 0, 0)])
    elif kind == "width":
        entity = msp.add_lwpolyline([(0, 0), (10, 0)], dxfattribs={"const_width": 2})
    elif kind == "legacy_width":
        # legacy POLYLINE: per-vertex start/end width
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.vertices[0].dxf.start_width = 0.5
    elif kind == "legacy_default_width":
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.dxf.default_start_width = 0.5
    elif kind == "legacy_spline_flag":
        # spline-fit / 3D / mesh flag bits (flags & 6)
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.dxf.flags = 4
    elif kind == "legacy_elevation":
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.dxf.elevation = (0, 0, 3)
    elif kind == "legacy_vertex_z":
        entity = msp.add_polyline2d([(0, 0), (10, 0)])
        entity.vertices[0].dxf.location = (0, 0, 2)
    elif kind == "nan_coords":
        entity = msp.add_line((0, 0), (float("nan"), 0))
    elif kind == "inf_coords":
        entity = msp.add_line((0, 0), (float("inf"), 0))
    else:
        entity = msp.add_line((0, 0), (10, 0), dxfattribs={"thickness": 3})
    result = _parse_document(doc)
    assert result.geometries == ()
    assert any(
        entity.dxf.handle in w and entity.dxftype() in w and "unsupported" in w
        for w in result.warnings
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", [
    "nested", "minsert", "clipped", "unsupported_member", "bulged_member", "elevated_insert",
    "missing_block", "empty_block", "degenerate_member", "nonfinite_transform",
])
def test_insert_refuses_whole_placement_instead_of_partial_geometry(kind):
    doc = ezdxf.new("R2010")
    doc.units = 4
    block = doc.blocks.new("BLOCK")
    block.add_line((0, 0), (10, 0))
    insert = doc.modelspace().add_blockref("BLOCK", (0, 0))
    if kind == "nested":
        inner = doc.blocks.new("INNER")
        inner.add_line((0, 2), (10, 2))
        block.add_blockref("INNER", (0, 0))
    elif kind == "minsert":
        insert.dxf.column_count = 2
        insert.dxf.column_spacing = 20
    elif kind == "clipped":
        from ezdxf.xclip import XClip
        XClip(insert).set_block_clipping_path([(0, 0), (5, 5)])
    elif kind == "unsupported_member":
        block.add_circle((0, 0), 2)
    elif kind == "bulged_member":
        block.add_lwpolyline([(0, 0, 1), (10, 0, 0)], format="xyb")
    elif kind == "missing_block":
        insert.dxf.name = "NO_SUCH_BLOCK"
    elif kind == "empty_block":
        doc.blocks.new("EMPTY")
        insert.dxf.name = "EMPTY"
    elif kind == "degenerate_member":
        block.add_polyline2d([(0, 0)])
    elif kind == "elevated_insert":
        insert.dxf.insert = (0, 0, 2)
    else:
        insert.dxf.xscale = float("inf")
    result = _parse_document(doc)
    expected_reason = {
        "nested": "nested INSERT", "minsert": "MINSERT", "clipped": "clipping",
        "unsupported_member": "CIRCLE", "bulged_member": "bulged",
        "elevated_insert": "elevation", "missing_block": "missing block definition",
        "empty_block": "empty block definition", "degenerate_member": "two vertices",
        "nonfinite_transform": "non-finite INSERT transform",
    }[kind]
    assert any(expected_reason in w for w in result.warnings)
    assert result.geometries == ()
    assert any(
        insert.dxf.handle in w and "INSERT" in w and "unsupported" in w
        for w in result.warnings
    )


@pytest.mark.unit
def test_insert_layer_zero_inherits_placement_nonzero_layer_preserved():
    doc = ezdxf.new("R2010")
    doc.units = 4
    block = doc.blocks.new("BLOCK")
    block.add_line((0, 0), (10, 0))
    block.add_line((0, 2), (10, 2), dxfattribs={"layer": "EXPLICIT"})
    doc.modelspace().add_blockref("BLOCK", (0, 0), dxfattribs={"layer": "WALL"})
    result = _parse_document(doc)
    assert [g.layer for g in result.geometries] == ["WALL", "EXPLICIT"]
    assert result.warnings == ()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["skip", "raise"])
def test_insert_transform_failure_never_leaks_partial_geometry(monkeypatch, failure):
    from ezdxf.entities import Insert

    doc = ezdxf.new("R2010")
    doc.units = 4
    block = doc.blocks.new("BLOCK")
    block.add_line((0, 0), (10, 0))
    block.add_line((0, 2), (10, 2))
    insert = doc.modelspace().add_blockref("BLOCK", (0, 0))
    original = Insert.virtual_entities

    def partial(self, *, skipped_entity_callback=None, **kwargs):
        members = list(original(self, **kwargs))
        yield members[0]
        if failure == "raise":
            raise ValueError("transform regression failure")
        skipped_entity_callback(members[1], "transform regression skipped")

    monkeypatch.setattr(Insert, "virtual_entities", partial)
    result = _parse_document(doc)
    assert result.geometries == ()
    assert any(
        insert.dxf.handle in warning and "transform regression" in warning
        for warning in result.warnings
    )


@pytest.mark.unit
def test_straight_legacy_polyline_preserves_vertices():
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_polyline2d([(0, 0), (10, 0), (10, 10)])
    result = _parse_document(doc)
    assert result.warnings == ()
    assert result.geometries[0].coordinates == [(0, 0), (10, 0), (10, 10)]


@pytest.mark.unit
def test_paperspace_content_never_leaks_into_modelspace_geometries():
    """Paperspace is counted but never measurable in V1; nothing placed there
    may become modelspace geometry, and the layout must be warned about
    (unsupported entities in paperspace are covered by the same blanket
    layout warning — paperspace is not parsed for measurable geometry at all).
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_line((0, 0), (10, 0))
    ps = doc.layouts.get("Layout1")
    ps.add_circle((0, 0), 5)
    ps.add_line((0, 0), (100, 0))
    result = _parse_document(doc)
    assert len(result.geometries) == 1, "only the modelspace line may be geometry"
    layout = next(s for s in result.sheets if s.sheet_ref == "paperspace:Layout1")
    assert layout.entity_count == 2
    assert layout.measurable_count == 0
    assert layout.measurable is False
    assert any("not measurable" in w for w in result.warnings)


@pytest.mark.unit
def test_modelspace_unsupported_entity_also_blocks_sheet_measurable_count():
    """The modelspace sheet summary counts refused entities, so a drawing that
    is entirely unsupported cannot masquerade as a clean empty measurable sheet.
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_circle((0, 0), 5)
    result = _parse_document(doc)
    sheet = result.sheets[0]
    assert sheet.entity_count == 1
    assert sheet.measurable_count == 0
    assert sheet.measurable is False
    assert any("CIRCLE" in w for w in result.warnings)


@pytest.mark.unit
def test_warns_instead_of_crashing_on_hand_edited_handleless_entity():
    """A hand-edited R12-style DXF whose entities carry no handle tag is the
    one input where _handle_of could raise inside warning formatting. ezdxf
    assigns deterministic handles when the tag is absent entirely — so parsing
    never crashes mid-warning: the circle is refused WITH its warning and the
    supported LINE is still captured with a real handle.
    (An explicitly EMPTY handle value is instead refused at load — see
    test_invalid_handle_is_a_structural_parse_error_not_a_crash.)
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_circle((0, 0), 5)
    stream = io.StringIO()
    doc.write(stream)
    raw = stream.getvalue()
    # Drop the whole handle tag+value line from both modelspace entities.
    stripped = re.sub(
        r"(  0\n(?:LINE|CIRCLE)\n)  5\n[0-9A-F]+\n", r"\1", raw, count=0,
    )
    assert stripped != raw, "handle tags not found to strip"
    result = parse_dxf(stripped.encode())
    assert any("CIRCLE" in w and "unsupported" in w for w in result.warnings)
    assert result.geometries, "supported LINE must still be captured with a handle"
    for geom in result.geometries:
        assert geom.source_handles[0].entity_ref not in ("", "0", None)


@pytest.mark.unit
def test_invalid_handle_is_a_structural_parse_error_not_a_crash():
    """Explicitly invalid handle values ("0" or empty) are refused whole-file
    by ezdxf at load time (DxfParseError) — never a crash mid-warning and
    never a silently normalized handle "0" attached to geometry.
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_line((0, 0), (10, 0))
    stream = io.StringIO()
    doc.write(stream)
    raw = stream.getvalue()
    head, sep, tail = raw.rpartition("  0\nLINE\n  5\n")
    handle, _marker, rest = tail.partition("\n")
    assert re.match(r"^[0-9A-F]+$", handle), f"unexpected handle {handle!r}"
    for bad in ("0\n", "\n", " \n"):
        with pytest.raises(DxfParseError):
            parse_dxf((head + sep + bad + rest).encode())


@pytest.mark.unit
def test_zero_length_line_is_captured_not_reinterpreted():
    """A zero-length LINE (two identical points) is honest input evidence: the
    parser must keep it as source geometry and NOT warn (it is supported
    straight-line semantics); downstream the wall detector refuses it as an
    edge candidate, so it can never contribute a fake wall.
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_line((5, 5), (5, 5))
    result = _parse_document(doc)
    assert len(result.geometries) == 1
    assert result.warnings == ()
    assert [tuple(p) for p in result.geometries[0].coordinates] == [(5.0, 5.0), (5.0, 5.0)]


@pytest.mark.unit
def test_inserted_member_coordinates_account_for_block_base_point():
    """The INSERT placement must align the block BASE POINT with the insertion
    point — a member drawn at block-local (0,0) with base_point (5,5) placed at
    (100,200) must land at (95,195). A parser that ignored base_point would
    double-shift or mis-place every real-world block.
    """
    doc = ezdxf.new("R2010")
    doc.units = 4
    block = doc.blocks.new("SEGMENT")
    block.block.dxf.base_point = (5, 5)
    member = block.add_line((0, 0), (10, 0))
    insert = doc.modelspace().add_blockref("SEGMENT", (100, 200))
    result = _parse_document(doc)
    assert result.warnings == ()
    assert len(result.geometries) == 1
    assert [tuple(p) for p in result.geometries[0].coordinates] == [
        (95.0, 195.0), (105.0, 195.0),
    ]
    assert [h.entity_ref for h in result.geometries[0].source_handles] == [
        insert.dxf.handle, member.dxf.handle,
    ]


@pytest.mark.unit
def test_source_digest_binds_original_file_bytes():
    import hashlib

    original = load("wall_plan.dxf")
    changed = original.replace(b"4000.0", b"5000.0", 1)
    assert changed != original
    first = parse_dxf(original)
    assert first.source_sha256 == hashlib.sha256(original).hexdigest()
    assert parse_dxf(original).source_sha256 == first.source_sha256
    assert parse_dxf(changed).source_sha256 != first.source_sha256


# ---------------------------------------------------------------------------
# T043 — TEXT/MTEXT label capture (evidence tokens, never geometry)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTextTokens:
    def test_room_label_is_captured_with_full_identity(self) -> None:
        result = parse("room_plan.dxf")
        assert len(result.text_tokens) == 1
        token = result.text_tokens[0]
        assert token.text == "KITCHEN"
        assert token.insertion == (1000.0, 1500.0)
        assert token.height == 200.0
        assert token.handle.sheet_ref == "modelspace"
        assert token.handle.format is SourceFormat.DXF_ENTITY
        assert token.handle.layer == "TEXT"
        # a REAL handle (present in the raw file bytes), not a positional id
        assert re.search(rb"^38$", load("room_plan.dxf"), re.M)
        assert token.handle.entity_ref == "38"
        assert token.handle.entity_ref not in ("", "0")

    def test_label_handle_is_stable_across_reparse(self) -> None:
        first = parse("room_plan.dxf")
        second = parse("room_plan.dxf")
        assert first.text_tokens == second.text_tokens, (
            "text tokens must be identical across re-parses"
        )
        assert (
            first.text_tokens[0].handle.entity_ref
            == second.text_tokens[0].handle.entity_ref
        )

    def test_labels_captured_in_modelspace_order(self) -> None:
        result = parse("two_room_plan.dxf")
        assert [t.text for t in result.text_tokens] == ["BEDROOM", "BATH"]

    def test_labels_are_evidence_never_geometry(self) -> None:
        """A room_plan with 8 wall lines + 1 label must produce exactly 8
        geometries — the label joins text_tokens, not the geometry rows, and
        must never appear as a warning either (it is honest evidence).
        """
        result = parse("room_plan.dxf")
        assert len(result.geometries) == 8
        assert result.warnings == ()
        # the sheet counts the token as an entity (it IS on the sheet)
        assert result.sheets[0].entity_count == 9
        assert result.sheets[0].measurable_count == 8

    def test_storey_labels_captured(self) -> None:
        result = parse("multi_storey_hint.dxf")
        assert [t.text for t in result.text_tokens] == ["ROOM L1", "ROOM L2"]

    def test_mtext_plain_text_char_height_and_insert(self) -> None:
        r"""MTEXT inline formatting codes (\P, \A1;) must reach the token as
        readable text — ezdxf 1.4's plain_text() — with the insertion point
        (dxf.insert.xy) and char height, not the raw code string.
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        mtext = doc.modelspace().add_mtext("MAIN \\P HALL")
        mtext.dxf.char_height = 150.0
        mtext.set_location((250.0, 400.0))
        result = _parse_document(doc)
        assert result.warnings == ()
        assert result.geometries == ()
        assert len(result.text_tokens) == 1
        token = result.text_tokens[0]
        assert token.text == "MAIN \n HALL", "inline \\P becomes a newline"
        assert token.insertion == (250.0, 400.0)
        assert token.height == 150.0
        assert token.handle.format is SourceFormat.DXF_ENTITY

    def test_mtext_char_height_absent_in_source_is_none(self) -> None:
        """A hand-edited MTEXT whose height code 40 never made it into the
        file must yield height=None (absence is honest), not a default.
        (ezdxf's writer always emits 40, so the tag is stripped by hand —
        the in-memory API cannot produce the absent case.)
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        doc.modelspace().add_mtext("ANONYMOUS")
        stream = io.StringIO()
        doc.write(stream)
        raw = stream.getvalue()
        needle = " 40\n2.5\n 71\n1\n  1\nANONYMOUS\n"
        assert needle in raw, "expected the char-height tag to strip"
        stripped = raw.replace(needle, " 71\n1\n  1\nANONYMOUS\n", 1)
        result = parse_dxf(stripped.encode())
        assert len(result.text_tokens) == 1
        assert result.text_tokens[0].height is None
        assert result.text_tokens[0].text == "ANONYMOUS"

    def test_text_without_height_tag_gets_none(self) -> None:
        """A hand-edited TEXT whose height code 40 never made it into the file
        must yield height=None (absence is honest), not a default.
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        doc.modelspace().add_text("HAND EDITED")
        stream = io.StringIO()
        doc.write(stream)
        raw = stream.getvalue()
        needle = " 40\n2.5\n  1\nHAND EDITED\n"
        assert needle in raw, "expected the height tag to strip"
        stripped = raw.replace(needle, "  1\nHAND EDITED\n", 1)
        result = parse_dxf(stripped.encode())
        assert len(result.text_tokens) == 1
        assert result.text_tokens[0].height is None
        assert result.text_tokens[0].text == "HAND EDITED"

    def test_rotated_label_is_captured(self) -> None:
        """Rotation is display-only: the insertion point is the anchor either
        way, so a rotated label is captured as-is, no warning.
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        text = doc.modelspace().add_text(
            "ROTATED", dxfattribs={"height": 100, "rotation": 30}
        )
        text.set_placement((1000.0, 1500.0))
        result = _parse_document(doc)
        assert result.warnings == ()
        assert [t.text for t in result.text_tokens] == ["ROTATED"]
        assert result.text_tokens[0].insertion == (1000.0, 1500.0)

    def test_empty_after_strip_text_warns_and_makes_no_phantom(self) -> None:
        doc = ezdxf.new("R2010")
        doc.units = 4
        entity = doc.modelspace().add_text("   ")
        entity.dxf.height = 50
        result = _parse_document(doc)
        assert result.text_tokens == ()
        assert result.geometries == ()
        assert any(
            entity.dxf.handle in w and "TEXT" in w and "empty text" in w
            for w in result.warnings
        )

    def test_whitespace_text_survives_strip(self) -> None:
        """Leading/trailing whitespace is display padding, not content: the
        stripped inner text is the token."""
        doc = ezdxf.new("R2010")
        doc.units = 4
        doc.modelspace().add_text("  STUDY  ", dxfattribs={"height": 100})
        result = _parse_document(doc)
        assert [t.text for t in result.text_tokens] == ["STUDY"]

    def test_text_with_nonzero_elevation_warns_and_makes_no_token(self) -> None:
        """T043 refinement: a label elevated out of the V1 plane has no honest
        plan position — warn + skip, never project it back onto the plane.
        (set via dxf.insert = (x, y, z), the documented ezdxf attribute.)
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        entity = doc.modelspace().add_text("FLOATING", dxfattribs={"height": 100})
        entity.dxf.insert = (5.0, 6.0, 7.0)
        result = _parse_document(doc)
        assert result.text_tokens == ()
        assert result.geometries == ()
        assert any(
            entity.dxf.handle in w and "TEXT" in w and "nonzero elevation" in w
            for w in result.warnings
        )

    def test_text_without_content_tag_is_a_warning_not_a_crash(self) -> None:
        """A hand-edited file whose TEXT lost its content tag (code 1) reads
        back as text='' in ezdxf 1.4 — the empty-text refusal covers it; the
        parser must warn, never crash, never emit a phantom token.
        """
        doc = ezdxf.new("R2010")
        doc.units = 4
        doc.modelspace().add_text("GHOST")
        stream = io.StringIO()
        doc.write(stream)
        raw = stream.getvalue()
        stripped = raw.replace("  1\nGHOST\n", "  1\n\n", 1)
        assert stripped != raw
        result = parse_dxf(stripped.encode())
        assert result.text_tokens == ()
        assert any("TEXT" in w and "empty text" in w for w in result.warnings)

    def test_text_tokens_are_deterministic(self) -> None:
        first = parse("two_room_plan.dxf")
        second = parse("two_room_plan.dxf")
        assert first.text_tokens == second.text_tokens
        assert first == second, "ParseResult must be byte-stable across re-parses"


# ---------------------------------------------------------------------------
# T045 — opening-block markers (name classification + INSERT-handle map)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpeningBlockNames:
    def test_insert_handles_map_to_block_names(self) -> None:
        names = block_names_by_insert_handle(load("opening_blocks.dxf"))
        assert names == {"40": "D1000", "42": "W1200"}

    def test_map_covers_exploded_geometry_leading_handles(self) -> None:
        """The T045 join: exploded door-leaf lines lead with the INSERT
        handle, so every INSERT handle that produced geometry must be a key
        of the map. (Bare LINE handles are not keys — INSERTs only.)
        """
        result = parse("opening_blocks.dxf")
        assert result.geometries, "fixture must explode into geometry"
        names = block_names_by_insert_handle(load("opening_blocks.dxf"))
        insert_handles = {
            g.source_handles[0].entity_ref
            for g in result.geometries
            if len(g.source_handles) == 2  # exploded: INSERT handle + member
        }
        assert insert_handles == {"40", "42"}
        assert insert_handles <= set(names), (
            "every exploded INSERT's handle must appear in the name map"
        )

    def test_opening_names_classified_exactly(self) -> None:
        cases = {
            "D1000": True, "W1200": True, "d1000": True, "w1200": True,
            "DOOR": True, "WIN": True, "DR": True, "door900": True,
            "WINDOW2100": True, "dr45": True,
            "WALLSEG": False, "X1000": False, "D1000LH": False,
            "KITCHEN": False, "WALL": False, "D 1000": False, "WIN-1200": False,
        }
        for name, expected in cases.items():
            assert is_opening_block_name(name) is expected, name

    def test_block_wall_fixture_is_not_opening_named(self) -> None:
        """WALLSEG geometry must keep exploding as plain wall geometry — the
        opening-name classifier must not touch the INSERT explosion path.
        """
        result = parse("block_wall.dxf")
        assert len(result.geometries) == 2
        names = block_names_by_insert_handle(load("block_wall.dxf"))
        assert all(
            not is_opening_block_name(n) for n in names.values()
        ), names
        assert result.warnings == ()

    def test_map_is_deterministic(self) -> None:
        data = load("opening_blocks.dxf")
        assert block_names_by_insert_handle(data) == (
            block_names_by_insert_handle(data)
        )

    def test_non_insert_entities_are_absent_from_map(self) -> None:
        """Only INSERT handles belong in the map — LINE handles must not
        appear (the openings detector joins on INSERT handles only).
        """
        names = block_names_by_insert_handle(load("room_plan.dxf"))
        assert names == {}

    def test_corrupt_file_raises_structural_error(self) -> None:
        with pytest.raises(DxfParseError):
            block_names_by_insert_handle(load("corrupt.dxf"))
