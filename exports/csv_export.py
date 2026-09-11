"""CSV export (T100 vertical slice) — priced BOQ rows → deterministic CSV bytes.

Pure module. Determinism contract (docs/domain-model.md ExportArtifact):
same inputs → byte-identical output, so the manifest sha256 is meaningful.
Quantities/money are formatted from Decimal/int — never floats.

Import-linter "Domain service layering" forbids exports→boq; the row shape is
therefore a structural Protocol (duck-typed at runtime, no import needed).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Protocol
from uuid import UUID

from core.domain.enums import BoqStatus, ExceptionSeverity
from core.provenance.records import ExceptionRecord
from core.units.money import apply_markup, multiply_rate

HEADER = [
    "sr_no",
    "code",
    "description",
    "unit",
    "quantity",
    "rate",
    "markup_bp",
    "total",
    "currency",
    "measurement_refs",
]

_QUANTITY_QUANTUM = Decimal("0.000001")  # NUMERIC(18,6) alignment


class CsvExportRow(Protocol):
    """Structural row shape exports needs (boq.assembly.BoqItemRow satisfies it)."""

    @property
    def code(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def unit(self) -> str: ...
    @property
    def quantity(self) -> Decimal: ...
    @property
    def rate_minor(self) -> int: ...
    @property
    def markup_bp(self) -> int: ...
    @property
    def total_minor(self) -> int: ...
    @property
    def currency(self) -> str: ...
    @property
    def measurement_ids(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ExportApproval:
    """Pure approval context, not authentication or an approval-issuing service.

    The future orchestrator must load a trusted persisted approval for this BOQ
    version and the COMPLETE unresolved exception scope. Do not accept this
    record directly from an API client. The digest binds the ordered row snapshot.
    Immutable artifacts, approval persistence and provenance sidecars are deferred.
    """

    boq_id: str
    approval_id: str
    rows_digest: str
    status: BoqStatus
    unresolved_exceptions: tuple[ExceptionRecord, ...] = ()


def rows_digest(rows: Sequence[CsvExportRow]) -> str:
    """Bind every serialized field, order and full measurement identity."""
    payload = [
        [r.code, r.description, r.unit, str(r.quantity), r.rate_minor,
         r.markup_bp, r.total_minor, r.currency, list(r.measurement_ids)]
        for r in rows
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def validate_export(
    rows: Sequence[CsvExportRow], approval: ExportApproval | None
) -> None:
    """The shared export gate (csv/xlsx/pdf writers all call this)."""
    if approval is None or not approval.boq_id.strip() or not approval.approval_id.strip():
        raise ValueError("export requires explicit approval context")
    if approval.status not in (BoqStatus.APPROVED, BoqStatus.EXPORTED):
        raise ValueError("export requires current APPROVED or EXPORTED status")
    # Fail closed: only explicitly INFO-level unresolved exceptions may pass;
    # a malformed severity (wrong case, None, non-member) is trust-boundary
    # data from the approval context and blocks the export.
    if any(e.severity is not ExceptionSeverity.INFO for e in approval.unresolved_exceptions):
        raise ValueError("export blocked by unresolved exceptions")
    if approval.rows_digest != rows_digest(rows):
        raise ValueError("approval snapshot is stale")
    if not rows:
        raise ValueError("no priced rows to export")
    if len({r.currency for r in rows}) != 1:
        raise ValueError("mixed currencies cannot be totalled")
    seen: set[str] = set()
    for row in rows:
        if row.currency not in {"INR", "USD", "EUR", "GBP"}:
            raise ValueError("unsupported two-decimal currency")
        if not row.quantity.is_finite() or row.quantity < 0:
            raise ValueError("invalid quantity")
        if not row.code.strip() or not row.unit.strip():
            raise ValueError("missing priced row code or unit")
        if any(type(n) is not int or n < 0
               for n in (row.rate_minor, row.markup_bp, row.total_minor)):
            raise ValueError("invalid integer pricing")
        if not row.measurement_ids:
            raise ValueError("missing measurement identity")
        for identity in row.measurement_ids:
            try:
                parsed_id = UUID(identity)
                if str(parsed_id) != identity or parsed_id.int == 0:
                    raise ValueError("measurement identity must be a nonzero canonical UUID")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("invalid measurement identity") from exc
            if identity in seen:
                raise ValueError("duplicate measurement identity")
            seen.add(identity)
        try:
            base = multiply_rate(row.quantity, row.rate_minor, currency=row.currency)
            expected = (base + apply_markup(base, row.markup_bp)).amount_minor
        except (ValueError, TypeError, DecimalException) as exc:
            raise ValueError("invalid pricing inputs") from exc
        if expected != row.total_minor:
            raise ValueError("total not recomputable")


def fmt_quantity(q: Decimal) -> str:
    return str(q.quantize(_QUANTITY_QUANTUM))


def fmt_money_minor(minor: int) -> str:
    """Minor units → major decimal string (2dp), via Decimal — never float."""
    return str((Decimal(minor) / Decimal(100)).quantize(Decimal("0.01")))


# Private aliases kept for backward compatibility with pre-Round-7 imports.
_validate_export = validate_export
_fmt_quantity = fmt_quantity
_fmt_money_minor = fmt_money_minor


def rows_to_csv(
    rows: Sequence[CsvExportRow], *, approval: ExportApproval | None = None
) -> str:
    """Render BOQ rows as CSV text. Row order = input order (deterministic)."""
    validate_export(rows, approval)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    total_minor = 0
    for i, row in enumerate(rows, start=1):
        writer.writerow(
            [
                i,
                row.code,
                row.description,
                row.unit,
                fmt_quantity(row.quantity),
                fmt_money_minor(row.rate_minor),
                row.markup_bp,
                fmt_money_minor(row.total_minor),
                row.currency,
                ";".join(row.measurement_ids),
            ]
        )
        total_minor += row.total_minor
    if rows:
        writer.writerow(
            ["", "TOTAL", "", "", "", "", "", fmt_money_minor(total_minor), rows[0].currency, ""]
        )
    return buf.getvalue()


def csv_bytes(
    rows: Sequence[CsvExportRow], *, approval: ExportApproval | None = None
) -> bytes:
    """UTF-8 CSV bytes (sha256-able for the export manifest)."""
    return rows_to_csv(rows, approval=approval).encode("utf-8")
