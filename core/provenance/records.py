"""Provenance model — the heart of the trust architecture.

Every quantity must be traceable to source drawing geometry. This module
defines the pure data structures for:
  * SourceHandle  — an anchor back to a raw entity in the original file
  * EvidenceLink  — a highlightable piece of evidence
  * MeasurementInputs — the exact inputs a rule consumed
  * AuditRecord  — append-only audit trail entry

Invariants (docs/domain-model.md §invariants) are checkable pure functions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.domain.enums import (
    AuditAction,
    ExceptionSeverity,
    SourceFormat,
)


@dataclass(frozen=True, slots=True)
class SourceHandle:
    """A stable anchor to a raw entity in the source drawing file.

    For DXF: the entity's dxf.handle (e.g. "2A1F") — stable across re-parses.
    For PDF vector: the path index within the page content stream.
    For raster: a crop/bbox region reference.
    """

    format: SourceFormat
    sheet_ref: str  # sheet id
    entity_ref: str  # dxf handle / path index / region id
    layer: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    """One highlightable evidence item attached to a measurement/element/exception."""

    kind: str  # geometry | text_token | image_region | dimension_annotation | scale_bar
    ref: str  # geometry id / token range / crop bbox spec
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RuleRef:
    """Which deterministic rule computed a value (replay contract)."""

    rule_id: str
    engine_version: str
    inputs_digest: str  # stable digest over the input refs


@dataclass(frozen=True, slots=True)
class MeasurementInputs:
    """Explicit input provenance for one measurement.

    refs: source handles / geometry ids consumed
    constants: named constants the rule used (e.g. wall height assumption)
    """

    refs: tuple[str, ...] = field(default_factory=tuple)
    constants: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        """Stable digest over the inputs — replay verification key."""
        import hashlib
        import json

        payload = json.dumps(
            {"refs": sorted(self.refs), "constants": self.constants},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExceptionRecord:
    """A catalogued exception; severity policy is data, not code."""

    code: str
    severity: ExceptionSeverity
    message: str
    sheet_id: str | None = None
    element_id: str | None = None
    measurement_id: str | None = None
    evidence: tuple[EvidenceLink, ...] = field(default_factory=tuple)
    ai_explanation: str | None = None  # AI may ONLY write this field


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Append-only audit trail row (docs/domain-model.md — ReviewDecision)."""

    at: str  # ISO-8601; caller supplies so this stays pure
    actor: str
    action: AuditAction
    subject_type: str
    subject_id: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    reason: str | None = None


def measurement_has_evidence(evidence: tuple[EvidenceLink, ...]) -> bool:
    """Invariant 1: a MEASURED value needs >=1 evidence link."""
    return len(evidence) >= 1
