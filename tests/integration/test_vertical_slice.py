"""Library integration: DXF → walls → measurements → BOQ → CSV.

One test walking the whole pipeline with a real fixture DXF and no mocks:
  ezdxf parse (stable handles) → scale gate (human-confirmed) → wall detection
  → deterministic measurements with evidence → BOQ assembly (rate + markup,
  integer minor units) → approval-gated CSV export bytes.

This does not exercise upload APIs, persistence, approval or browser E2E;
passing this test alone does not satisfy the Round 3 exit criterion.
"""
from __future__ import annotations

import csv
import dataclasses
import io
from decimal import Decimal

import ezdxf
import pytest

from boq.assembly import CatalogueRate, assemble_item, validate_rows
from core.domain.enums import (
    BoqStatus,
    MeasurementState,
    QuantityType,
    ScaleCalibrationStatus,
)
from core.units.geometry_units import ScaleCalibration
from exports.csv_export import ExportApproval, csv_bytes, rows_digest
from ingestion.dxf import parse_dxf
from takeoff.engine import measure_parsed

FIXTURE = (
    __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "dxf" / "wall_plan.dxf"
)


class TestVerticalSlice:
    @pytest.mark.parametrize("placed_block", [False, True])
    def test_dxf_to_csv_end_to_end(self, placed_block: bool) -> None:
        # 1. PARSE — real DXF, real ezdxf, stable handles
        data = FIXTURE.read_bytes()
        if placed_block:
            doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
            modelspace = doc.modelspace()
            block = doc.blocks.new("PLACED_WALLS")
            for entity in list(modelspace):
                modelspace.move_to_layout(entity, block)
            modelspace.add_blockref("PLACED_WALLS", (12000, 7000), dxfattribs={"rotation": 90})
            stream = io.StringIO()
            doc.write(stream)
            data = stream.getvalue().encode("utf-8")
        result = parse_dxf(data)
        assert result.drawing_units == "mm"
        assert len(result.geometries) >= 4  # 2 walls, each 2 parallel faces
        walls = [g for g in result.geometries if g.layer == "WALL"]
        assert walls, "fixture must contain WALL-layer geometry"

        # 2. SCALE GATE — human-confirmed calibration (never auto-applied)
        calibration = ScaleCalibration(
            sheet_id="modelspace",
            status=ScaleCalibrationStatus.CONFIRMED,
            units_per_drawing_unit=Decimal("1.0"),
            method="detected_from_dxf_units",
            confirmed_by="estimator@example.com",
        )

        # 3. MEASURE — deterministic engine run, consuming the FULL parse result
        # (warnings, raw-source identity) so extraction refusals cannot be lost.
        out = measure_parsed(
            result,
            sheet_id="modelspace",
            calibration=calibration,
            max_wall_thickness=250,
        )
        lengths = [m for m in out.measurements if m.quantity_type is QuantityType.LENGTH]
        assert sorted(m.value for m in lengths) == [Decimal("4"), Decimal("6")]
        assert out.exceptions == ()
        for m in out.measurements:
            # Round 5: walls carry an honest MEASURED_ZERO opening-count row
            # when no openings are drawn; every other row is MEASURED.
            assert m.state in (MeasurementState.MEASURED,
                               MeasurementState.MEASURED_ZERO)
            assert m.evidence, "invariant 1: no measurement without evidence"
            assert m.evidence[0].kind == "geometry"
        counts = [m for m in out.measurements if m.quantity_type.value == "count"]
        assert len(counts) == 2  # one per wall, both zero
        assert all(m.value == Decimal("0") for m in counts)

        # 4. BOQ — map wall lengths to a catalogue rate (integer minor units)
        rate = CatalogueRate(
            catalogue_item_id="cat-brick-230",
            code="2.1.1",
            description="Brick wall 230mm thick",
            unit="m",
            rate_minor=85_000,  # ₹850.00 per m
            markup_bp=750,
        )
        row = assemble_item(lengths, rate)
        assert row.quantity == sum(m.value for m in lengths) == Decimal("10")
        assert row.total_minor == 913750
        assert validate_rows([row]) == []  # invariant 5 holds

        # 5. EXPORT — deterministic CSV bytes behind the trust gate
        with pytest.raises(ValueError, match="approval"):
            csv_bytes([row])  # ungated export is refused
        approval = ExportApproval(
            boq_id="boq-1",
            approval_id="approval-1",
            rows_digest=rows_digest([row]),
            status=BoqStatus.APPROVED,
        )
        data = csv_bytes([row], approval=approval)
        text = data.decode("utf-8")
        assert csv_bytes([row], approval=approval) == data, "export must be byte-deterministic"
        with pytest.raises(ValueError, match="stale"):
            csv_bytes(  # any row mutation invalidates the approval snapshot
                [dataclasses.replace(row, description="changed")], approval=approval)
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0][0] == "sr_no"
        assert rows[1][4] == "10.000000"
        assert rows[1][7] == "9137.50"
        assert rows[-1][1] == "TOTAL"
        assert rows[-1][7] == "9137.50"
