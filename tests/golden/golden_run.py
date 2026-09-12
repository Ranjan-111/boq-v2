"""T121 — golden-run regression suite: canonical replay docs + regeneration.

docs/architecture.md release gate #2: "Determinism: golden-run replay
byte-identical quantities for all fixtures." docs/testing-strategy.md §1:
"Any diff = test failure, not a 'note'."

WHAT THIS MODULE IS
-------------------
The single source of truth for how a golden document is PRODUCED. The test
suite (test_golden_runs.py) and the regeneration entry point (this file, run
with ``--update`` via ``make golden-update``) both call :func:`build_document`,
so a golden file and its comparison can never diverge in shape.

DRIVING CONVENTION (mirrors the real pipeline, never invents inputs)
--------------------------------------------------------------------
* DXF  : parse_dxf + block_names_by_insert_handle -> measure_parsed with a
         CONFIRMED 1.0 mm-per-drawing-unit calibration (detected_from_dxf_units),
         max_wall_thickness=250 (the run-service default). Mirrors
         tests/unit/test_full_engine.py::_run.
* PDF  : parse_pdf -> measure_parsed with the CONFIRMED 1:100 bar-scale
         calibration, emit_candidates=True, drawing_units="mm" — exactly the
         backend run-service PDF path (backend/app/services/run_service.py:
         the calibration is the complete physical ratio, so the point-size
         base is the identity).
* raster: parse_raster -> measure_parsed. The parser honestly reports
         drawing_units="unknown", so the engine REFUSES to measure — that
         refusal (BLOCKING scale_unconfirmed) is the honest output for a
         raster sheet and is goldened, never skipped.
* Parse-refused fixtures (corrupt.dxf / corrupt.pdf / truncated.png /
  oversized.png): the parser's loud refusal IS the outcome. The golden records
  the refusal and the error TYPE (our public exception class); the verbatim
  message text belongs to ezdxf/pdfplumber/Pillow, not to this contract, so
  it is deliberately not part of the byte-stable document.

SERIALIZER RULES (binding — see docs/testing-strategy.md §8)
------------------------------------------------------------
* run-level arrays (measurements, exceptions, elements) are recorded in
  ENGINE order. That order is itself a pinned contract: element_index indexes
  RunOutput.elements, and candidate order follows detector output order
  (TestCandidateDeterminism). Re-sorting them could hide a real ordering
  change — the one thing a golden suite must never do. Each element records
  its index explicitly.
* per-measurement ``evidence`` and ``inputs`` are content multisets: they are
  sorted canonically (kind, ref, note) / lexicographic, matching the digest's
  own sorted-refs canonicalization. Their order is not a pinned contract.
* value is the EXACT Decimal string (str(value)) — never float(); money and
  quantity drift must be visible to the last digit.
* every array inside a JSON object is keyed through json.dumps(sort_keys=True)
  so key order can never be a false diff.
* the run's full replay inputs (fixture sha256, sheet, calibration factor and
  method, drawing units, max_wall_thickness, emit_candidates, block names)
  are recorded INSIDE the doc so replay is fully specified.

DELIBERATE DRIFT
----------------
Behavior changes are legitimate, but they must travel with an ENGINE_VERSION
bump (docs/domain-model.md: old runs replay by their stamped version). The
contract enforced by the tests:

  * golden.engine_version == current ENGINE_VERSION and bytes differ
      -> UNINTENDED drift (behavior moved under the same version label).
         Fix: revert, or bump ENGINE_VERSION first.
  * golden.engine_version != current ENGINE_VERSION and bytes differ
      -> the bump happened; goldens are stale. Fix: ``make golden-update``.

Regeneration is deterministic: running ``make golden-update`` twice produces
byte-identical files (git diff empty the second time).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.geometry import ParseResult
from core.provenance.records import EvidenceLink, ExceptionRecord
from core.units.geometry_units import ScaleCalibration
from takeoff.engine import MeasurementRecord, RunOutput
from takeoff.rules import ENGINE_VERSION

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
DATA_DIR = Path(__file__).resolve().parent / "data"
GOLDEN_SCHEMA = "golden-run-v1"


def _calibration_doc(calibration: ScaleCalibration) -> dict[str, Any]:
    factor = calibration.units_per_drawing_unit
    return {
        "sheet_id": calibration.sheet_id,
        "status": calibration.status.value,
        # exact Decimal string — never float() — so replay is byte-specified
        "units_per_drawing_unit": None if factor is None else str(factor),
        "method": calibration.method,
    }


def _evidence_doc(evidence: tuple[EvidenceLink, ...]) -> list[dict[str, str | None]]:
    """Sorted by (kind, ref, note): a content multiset, order not contractual."""
    ordered = sorted(evidence, key=lambda link: (link.kind, link.ref, link.note or ""))
    return [
        {"kind": link.kind, "ref": link.ref, "note": link.note} for link in ordered
    ]


def _measurement_doc(m: MeasurementRecord) -> dict[str, Any]:
    centerline = (
        [[float(x), float(y)] for x, y in m.centerline] if m.centerline is not None else None
    )
    return {
        "label": m.label,
        "quantity_type": m.quantity_type.value,
        "value": str(m.value),  # exact Decimal string — byte-identical goldens
        "unit": m.unit.value,
        "rule_id": m.rule_id,
        "engine_version": m.engine_version,
        "inputs_digest": m.inputs_digest,
        "measurement_id": m.measurement_id,
        "state": m.state.value,
        "element_type": m.element_type.value,
        "evidence": _evidence_doc(m.evidence),
        "inputs": sorted(m.inputs),
        "element_index": m.element_index,
        "centerline": centerline,
        "thickness": m.thickness,
    }


def _exception_doc(exc: ExceptionRecord) -> dict[str, Any]:
    # ExceptionRecord.code is typed str (the engine's _exc may widen an enum
    # through it); at runtime it IS the enum value here — normalize via str()
    # at the boundary so serialization never depends on the declared type.
    return {
        "code": str(exc.code.value if hasattr(exc.code, "value") else exc.code),
        "severity": str(exc.severity.value),
        "message": exc.message,
        "sheet_id": exc.sheet_id,
        "element_id": exc.element_id,
        "measurement_id": exc.measurement_id,
        "evidence": _evidence_doc(exc.evidence),
        "ai_explanation": exc.ai_explanation,
    }


def _parse_doc(parsed: ParseResult) -> dict[str, Any]:
    return {
        "drawing_units": parsed.drawing_units,
        "geometry_count": len(parsed.geometries),
        "text_token_count": len(parsed.text_tokens),
        "sheet_count": len(parsed.sheets),
        "warnings": list(parsed.warnings),
        "source_sha256": parsed.source_sha256,
        "text_tokens": [t.to_json() for t in parsed.text_tokens],
        "sheets": [s.to_json() for s in parsed.sheets],
    }


def _run_output_doc(out: RunOutput) -> dict[str, Any]:
    elements = [
        {
            "index": i,
            "element_type": el.element_type.value,
            "type_source": el.type_source.value,
            "label": el.label,
            "geometry": el.geometry.to_json(),
        }
        for i, el in enumerate(out.elements)  # ENGINE order — element_index contract
    ]
    return {
        "engine_version": out.engine_version,
        "measurements": [_measurement_doc(m) for m in out.measurements],  # engine order
        "exceptions": [_exception_doc(e) for e in out.exceptions],  # engine order
        "elements": elements,  # engine order + explicit index
    }


def serialize_document(doc: dict[str, Any]) -> str:
    """Canonical byte form: sorted keys, 2-space indent, trailing newline.

    The same function produces the committed golden and the live comparison
    string, so the test is a byte comparison of canonical serializations,
    not a dict ==.
    """
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# The golden case registry — every committed fixture, no skips.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    """One fully-specified run: a fixture, a sheet, a confirmed calibration."""

    case_id: str  # file stem under tests/golden/data/
    fmt: str  # dxf | pdf | raster
    fixture: str  # path relative to tests/fixtures/
    sheet_id: str
    calibration: ScaleCalibration
    max_wall_thickness: float | None = 250.0
    emit_candidates: bool = False
    drawing_units: str | None = None  # None -> the parse's own units


def _dxf_calibration() -> ScaleCalibration:
    from core.domain.enums import ScaleCalibrationStatus

    return ScaleCalibration(
        sheet_id="modelspace",
        status=ScaleCalibrationStatus.CONFIRMED,
        units_per_drawing_unit=Decimal("1.0"),
        method="detected_from_dxf_units",
    )


def _pdf_calibration(sheet_id: str) -> ScaleCalibration:
    from core.domain.enums import ScaleCalibrationStatus

    return ScaleCalibration(
        sheet_id=sheet_id,
        status=ScaleCalibrationStatus.CONFIRMED,
        # the committed 1:100 bar-scale proposal: 100 * 25.4 / 72 mm/pt
        units_per_drawing_unit=Decimal("35.2777777778"),
        method="bar_scale_detected",
    )


def _raster_calibration() -> ScaleCalibration:
    from core.domain.enums import ScaleCalibrationStatus

    return ScaleCalibration(
        sheet_id="image:1",
        status=ScaleCalibrationStatus.CONFIRMED,
        units_per_drawing_unit=Decimal("1.0"),
        method="user_known_ratio",
    )


def _registry() -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for name in sorted(p.name for p in (FIXTURES_DIR / "dxf").glob("*.dxf")):
        cases.append(GoldenCase(
            case_id=f"dxf__{Path(name).stem}__modelspace", fmt="dxf",
            fixture=f"dxf/{name}", sheet_id="modelspace",
            calibration=_dxf_calibration(),
        ))
    for name in sorted(p.name for p in (FIXTURES_DIR / "pdf").glob("*.pdf")):
        stem = Path(name).stem
        sheets = _pdf_sheets(name)
        for sheet_id in sheets:  # every sheet the parser reports gets a run
            slug = sheet_id.replace(":", "-")
            cases.append(GoldenCase(
                case_id=f"pdf__{stem}__{slug}", fmt="pdf",
                fixture=f"pdf/{name}", sheet_id=sheet_id,
                calibration=_pdf_calibration(sheet_id),
                emit_candidates=True,  # the run-service PDF path (R8)
                drawing_units="mm",  # bar-scale calibration bakes the point size
            ))
    for name in sorted(p.name for p in (FIXTURES_DIR / "raster").iterdir()):
        if name.startswith("."):
            continue
        cases.append(GoldenCase(
            case_id=f"raster__{Path(name).stem}__image-1", fmt="raster",
            fixture=f"raster/{name}", sheet_id="image:1",
            calibration=_raster_calibration(),
        ))
    return cases


def _pdf_sheets(name: str) -> list[str]:
    """Sheet refs of a PDF fixture (in parse order); corrupt files refuse."""
    from ingestion.pdf import PdfParseError, parse_pdf

    try:
        parsed = parse_pdf((FIXTURES_DIR / "pdf" / name).read_bytes())
    except PdfParseError:
        return ["page:0"]  # refused at parse; the sheet never gets consulted
    return [s.sheet_ref for s in parsed.sheets]


def _parse_for(case: GoldenCase) -> ParseResult:
    """Parse the fixture bytes; a loud parser refusal propagates (it IS the
    honest outcome for corrupt fixtures — caught by build_document)."""
    data = (FIXTURES_DIR / case.fixture).read_bytes()
    if case.fmt == "dxf":
        from ingestion.dxf import parse_dxf

        return parse_dxf(data)
    if case.fmt == "pdf":
        from ingestion.pdf import parse_pdf

        return parse_pdf(data)
    from ingestion.raster import parse_raster

    return parse_raster(data)


def _block_names(case: GoldenCase, data: bytes) -> dict[str, str]:
    if case.fmt != "dxf":
        return {}
    from ingestion.dxf import block_names_by_insert_handle

    return block_names_by_insert_handle(data)


def build_document(case: GoldenCase) -> dict[str, Any]:
    """Drive the real pipeline for one case and serialize the canonical doc."""
    data = (FIXTURES_DIR / case.fixture).read_bytes()
    doc: dict[str, Any] = {
        "schema": GOLDEN_SCHEMA,
        "case_id": case.case_id,
        "fixture": case.fixture,
        "fixture_sha256": hashlib.sha256(data).hexdigest(),
        "format": case.fmt,
        "current_engine_version": ENGINE_VERSION,
    }
    try:
        parsed = _parse_for(case)
    except ValueError as exc:  # DxfParseError/PdfParseError/RasterParseError
        # The parser's loud refusal is the honest outcome for this fixture.
        # Our contract is the refusal + its public type, not the third-party
        # message text (ezdxf/pdfplumber/Pillow own that).
        doc["parse_refused"] = True
        doc["parse_error_type"] = type(exc).__name__
        return doc

    from takeoff.engine import measure_parsed

    block_names = _block_names(case, data)
    out = measure_parsed(
        parsed,
        sheet_id=case.sheet_id,
        calibration=case.calibration,
        max_wall_thickness=case.max_wall_thickness,
        block_names=block_names or None,
        emit_candidates=case.emit_candidates,
        drawing_units=case.drawing_units,
    )
    doc["run_inputs"] = {
        "sheet_id": case.sheet_id,
        "calibration": _calibration_doc(case.calibration),
        "drawing_units": (case.drawing_units or parsed.drawing_units),
        "drawing_units_override": case.drawing_units,
        "max_wall_thickness": case.max_wall_thickness,
        "emit_candidates": case.emit_candidates,
        "block_names": dict(sorted(block_names.items())),
    }
    doc["parse"] = _parse_doc(parsed)
    doc.update(_run_output_doc(out))
    return doc


def build_case_text(case: GoldenCase) -> str:
    return serialize_document(build_document(case))


def golden_path(case: GoldenCase) -> Path:
    return DATA_DIR / f"{case.case_id}.json"


def all_cases() -> list[GoldenCase]:
    return _registry()


def main(update: bool) -> int:
    cases = all_cases()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for case in cases:
        text = build_case_text(case)
        path = golden_path(case)
        if update:
            path.write_text(text)
            print(f"wrote {path.relative_to(DATA_DIR)}")
        else:
            live = path.read_text() if path.exists() else ""
            status = "OK" if live == text else "DRIFT"
            print(f"{status}  {case.case_id}")
    print(f"engine_version={ENGINE_VERSION}  cases={len(cases)}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(update="--update" in sys.argv))
