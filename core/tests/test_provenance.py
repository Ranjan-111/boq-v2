"""Unit tests for provenance records + invariants."""
from __future__ import annotations

import json

from core.domain.enums import (
    AuditAction,
    ExceptionSeverity,
    SourceFormat,
)
from core.provenance.records import (
    AuditRecord,
    EvidenceLink,
    ExceptionRecord,
    MeasurementInputs,
    RuleRef,
    SourceHandle,
    measurement_has_evidence,
)


class TestSourceHandle:
    def test_dxf_handle_is_captured(self) -> None:
        h = SourceHandle(
            format=SourceFormat.DXF_ENTITY, sheet_ref="s1", entity_ref="2A1F", layer="WALL"
        )
        assert h.entity_ref == "2A1F"

    def test_frozen(self) -> None:
        import dataclasses

        h = SourceHandle(format=SourceFormat.PDF_VECTOR, sheet_ref="s", entity_ref="p1")
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            h.entity_ref = "x"  # type: ignore[misc]


class TestMeasurementInputsDigest:
    def test_digest_is_order_insensitive_over_refs(self) -> None:
        a = MeasurementInputs(refs=("g1", "g2"))
        b = MeasurementInputs(refs=("g2", "g1"))
        assert a.digest() == b.digest()

    def test_digest_changes_with_inputs(self) -> None:
        a = MeasurementInputs(refs=("g1",), constants={"height_mm": 3000})
        b = MeasurementInputs(refs=("g1",), constants={"height_mm": 3100})
        assert a.digest() != b.digest()

    def test_digest_is_stable(self) -> None:
        a = MeasurementInputs(refs=("g1", "g2"), constants={"k": 1})
        assert a.digest() == MeasurementInputs(
            refs=("g2", "g1"), constants={"k": 1}
        ).digest()


class TestInvariant1:
    def test_evidence_required(self) -> None:
        assert measurement_has_evidence((EvidenceLink(kind="geometry", ref="g1"),))
        assert not measurement_has_evidence(())


class TestExceptionRecord:
    def test_ai_can_only_explain(self) -> None:
        e = ExceptionRecord(
            code="scale_unconfirmed",
            severity=ExceptionSeverity.BLOCKING,
            message="Scale not confirmed for sheet s1",
            ai_explanation="The scale bar on this sheet is illegible; confirm manually.",
        )
        assert e.ai_explanation is not None
        # NOTE: ExceptionRecord has no quantity/value fields at all — the data
        # structure itself enforces 'AI cannot write numbers into quantities'.


class TestAuditRecord:
    def test_before_after_diff_shape(self) -> None:
        rec = AuditRecord(
            actor="user-1",
            action=AuditAction.CORRECT_QUANTITY,
            subject_type="measurement",
            subject_id="m-1",
            before={"value": "12.4"},
            after={"value": "12.9"},
            reason="wall length misread",
            at="2026-09-08T00:00:00Z",
        )
        assert json.dumps(rec.before, sort_keys=True)  # JSON-serializable


class TestRuleRef:
    def test_replay_contract(self) -> None:
        r = RuleRef(
            rule_id="wall_centerline_length", engine_version="0.1.0", inputs_digest="ab12"
        )
        assert r.rule_id == "wall_centerline_length"
