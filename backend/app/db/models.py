"""SQLAlchemy ORM models — every entity in docs/domain-model.md.

Naming: snake_case tables prefixed by bounded context (project_, drawing_,
run_, meas_, exc_, cat_, rate_, boq_, export_, audit_, job_).
All ids: native UUID (SQLAlchemy Uuid). Money: INTEGER minor units.
Quantities: NUMERIC(18,6). Geometry coordinates: JSONB.

IMPORTANT: columns like meas.value have NO default and are written ONLY by
the deterministic engine path or an audited human correction. AI suggestion
tables are separate by construction.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from backend.app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="estimator")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Project context
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(200))
    region_code: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_by: Mapped[str] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Storey(Base):
    __tablename__ = "storeys"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    elevation_mm: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DrawingFile(Base):
    __tablename__ = "drawing_files"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)  # pdf|dxf|raster
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uploaded_by: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending|parsing|parsed|failed


class DrawingSheet(Base):
    __tablename__ = "drawing_sheets"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    drawing_file_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("drawing_files.id"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    sheet_type: Mapped[str | None] = mapped_column(String(20))
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))

    __table_args__ = (UniqueConstraint("drawing_file_id", "page_number"),)


class ScaleCalibrationModel(Base):
    __tablename__ = "scale_calibrations"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    sheet_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("drawing_sheets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # proposed|confirmed|unknown
    method: Mapped[str | None] = mapped_column(String(40))
    units_per_drawing_unit: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confirmed_by: Mapped[str | None] = mapped_column(Uuid, ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Runs, elements, geometry, measurements
# ---------------------------------------------------------------------------


class MeasurementRun(Base):
    __tablename__ = "measurement_runs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    engine_version: Mapped[str | None] = mapped_column(String(20))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Element(Base):
    __tablename__ = "elements"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("measurement_runs.id"), nullable=False, index=True
    )
    sheet_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("drawing_sheets.id"), nullable=False, index=True
    )
    storey_id: Mapped[str | None] = mapped_column(Uuid, ForeignKey("storeys.id"))
    element_type: Mapped[str] = mapped_column(String(40), nullable=False)
    type_source: Mapped[str] = mapped_column(String(24), nullable=False)
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    ai_model: Mapped[str | None] = mapped_column(String(60))
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(512))


class GeometryModel(Base):
    __tablename__ = "geometries"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    element_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("elements.id"), nullable=False, index=True
    )
    geom_type: Mapped[str] = mapped_column(String(16), nullable=False)
    coordinates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    source_handles: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False
    )  # [{format, sheet_ref, entity_ref, layer?}]
    derived_from: Mapped[list[str] | None] = mapped_column(JSONB)


class MeasurementModel(Base):
    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("measurement_runs.id"), nullable=False, index=True
    )
    element_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("elements.id"), nullable=False, index=True
    )
    quantity_type: Mapped[str] = mapped_column(String(12), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(8))
    rule_id: Mapped[str | None] = mapped_column(String(80))
    engine_version: Mapped[str | None] = mapped_column(String(20))
    inputs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    inputs_digest: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked")
    correction_of: Mapped[str | None] = mapped_column(Uuid, ForeignKey("measurements.id"))
    corrected_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('measured','measured_zero','needs_review','not_measurable','blocked')",
            name="meas_state_ck",
        ),
    )


class ExceptionModel(Base):
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("measurement_runs.id"), nullable=False, index=True
    )
    measurement_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("measurements.id"), index=True
    )
    element_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("elements.id"), index=True
    )
    sheet_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("drawing_sheets.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[Any] | None] = mapped_column(JSONB)
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('blocking','review','info')", name="exc_severity_ck"
        ),
        Index("ix_exceptions_run_severity", "run_id", "severity"),
    )


class EvidenceLinkModel(Base):
    __tablename__ = "evidence_links"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[str] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    note: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        Index("ix_evidence_subject", "subject_type", "subject_id"),
    )


# ---------------------------------------------------------------------------
# Catalogue & rates
# ---------------------------------------------------------------------------


class CatalogueItem(Base):
    __tablename__ = "catalogue_items"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    region_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    category_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    source_attribution: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("region_code", "code", name="uq_catalogue_region_code"),
    )


class RateModel(Base):
    __tablename__ = "rates"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    catalogue_item_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("catalogue_items.id"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entered_by: Mapped[str | None] = mapped_column(Uuid, ForeignKey("users.id"))
    imported_from: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="rate_nonneg_ck"),
        CheckConstraint(
            "scope IN ('default','project','vendor')", name="rate_scope_ck"
        ),
    )


# ---------------------------------------------------------------------------
# BOQ
# ---------------------------------------------------------------------------


class BoqModel(Base):
    __tablename__ = "boqs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    from_run_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("measurement_runs.id")
    )
    approved_by: Mapped[str | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','in_review','reviewed','approved','stale_approved','exported')",
            name="boq_status_ck",
        ),
    )


class BoqSection(Base):
    __tablename__ = "boq_sections"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    boq_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("boqs.id"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(Uuid, ForeignKey("boq_sections.id"))
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BoqItem(Base):
    __tablename__ = "boq_items"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    section_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("boq_sections.id"), nullable=False, index=True
    )
    origin: Mapped[str] = mapped_column(String(10), nullable=False)  # mapped|manual|pc_sum
    catalogue_item_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("catalogue_items.id")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    rate_minor: Mapped[int | None] = mapped_column(Integer)
    rate_scope: Mapped[str | None] = mapped_column(String(10))
    markup_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_minor: Mapped[int | None] = mapped_column(Integer)
    measurement_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "origin IN ('mapped','manual','pc_sum')", name="boq_item_origin_ck"
        ),
    )


# ---------------------------------------------------------------------------
# Exports & audit
# ---------------------------------------------------------------------------


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    boq_id: Mapped[str] = mapped_column(Uuid, ForeignKey("boqs.id"), nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(12), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEntry(Base):
    """Append-only audit trail. Application role has INSERT/SELECT grants only."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(Uuid, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(Uuid, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)


class AiSuggestion(Base):
    """AI proposal store — AI writes ONLY here (docs/domain-model.md invariant 3)."""

    __tablename__ = "ai_suggestions"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("measurement_runs.id"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(Uuid, nullable=False)
    suggestion_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt_log_id: Mapped[str | None] = mapped_column(Uuid)
    accepted: Mapped[bool | None] = mapped_column()  # NULL=unreviewed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ai_conf_ck"),
    )


class JobRun(Base):
    """Postgres-backed job queue row (docs/architecture.md §D)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), unique=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="job_status_ck",
        ),
        Index("ix_jobs_claim", "status", "created_at"),
    )
