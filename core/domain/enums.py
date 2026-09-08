"""Domain enums — the single vocabulary of the product.

Source of truth: docs/domain-model.md. State machines live in
core/domain/states.py; these enums define the VALUES only.
"""
from __future__ import annotations

from enum import StrEnum


class DrawingFormat(StrEnum):
    PDF = "pdf"
    DXF = "dxf"
    RASTER = "raster"


class SheetType(StrEnum):
    PLAN = "plan"
    SECTION = "section"
    ELEVATION = "elevation"
    DETAIL = "detail"
    SCHEDULE = "schedule"
    UNKNOWN = "unknown"


class ScaleCalibrationStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class ScaleMethod(StrEnum):
    DETECTED_FROM_DXF_UNITS = "detected_from_dxf_units"
    BAR_SCALE_DETECTED = "bar_scale_detected"
    USER_TWO_POINT = "user_two_point"
    USER_KNOWN_RATIO = "user_known_ratio"


class ElementType(StrEnum):
    WALL = "wall"
    ROOM = "room"
    SLAB = "slab"
    DOOR = "door"
    WINDOW = "window"
    OPENING = "opening"
    FLOOR_FINISH = "floor_finish"
    OTHER = "other"


class ElementTypeSource(StrEnum):
    GEOMETRY_DETERMINISTIC = "geometry_deterministic"
    AI_CLASSIFIED = "ai_classified"
    HUMAN_SET = "human_set"


class GeomType(StrEnum):
    POINT = "point"
    LINE = "line"
    POLYLINE = "polyline"
    POLYGON = "polygon"
    MULTI_POLYGON = "multi_polygon"


class SourceFormat(StrEnum):
    PDF_VECTOR = "pdf_vector"
    DXF_ENTITY = "dxf_entity"
    RASTER_REGION = "raster_region"


class QuantityType(StrEnum):
    LENGTH = "length"
    AREA = "area"
    COUNT = "count"
    VOLUME = "volume"


class MeasurementUnit(StrEnum):
    MM = "mm"
    M = "m"
    M2 = "m2"
    M3 = "m3"
    COUNT = "count"


class MeasurementState(StrEnum):
    """docs/domain-model.md — Measurement states."""

    MEASURED = "measured"
    MEASURED_ZERO = "measured_zero"
    NEEDS_REVIEW = "needs_review"
    NOT_MEASURABLE = "not_measurable"
    BLOCKED = "blocked"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_EXCEPTIONS = "completed_with_exceptions"
    FAILED = "failed"


class ExceptionSeverity(StrEnum):
    BLOCKING = "blocking"
    REVIEW = "review"
    INFO = "info"


class ExceptionCode(StrEnum):
    """Catalogued exception codes. Extensible; severity policy lives in rules."""

    SCALE_UNCONFIRMED = "scale_unconfirmed"
    OPEN_POLYLINE = "open_polyline"
    SELF_INTERSECTING = "self_intersecting"
    OVERLAP_DETECTED = "overlap_detected"
    AMBIGUOUS_SHEET = "ambiguous_sheet"
    AI_LOW_CONFIDENCE = "ai_low_confidence"
    UNMAPPED_MEASUREMENT = "unmapped_measurement"
    MISSING_RATE = "missing_rate"
    MISSING_EVIDENCE = "missing_evidence"
    PARSE_INCOMPLETE = "parse_incomplete"


class RateScope(StrEnum):
    DEFAULT = "default"
    PROJECT = "project"
    VENDOR = "vendor"


class BoqStatus(StrEnum):
    """docs/domain-model.md — Approval states."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    STALE_APPROVED = "stale_approved"
    EXPORTED = "exported"


class BoqItemOrigin(StrEnum):
    MAPPED = "mapped"
    MANUAL = "manual"
    PC_SUM = "pc_sum"


class UserRole(StrEnum):
    OWNER = "owner"
    ESTIMATOR = "estimator"
    VIEWER = "viewer"


class AuditAction(StrEnum):
    CONFIRM_SCALE = "confirm_scale"
    OVERRIDE_ELEMENT_TYPE = "override_element_type"
    ACCEPT_MEASUREMENT = "accept_measurement"
    CORRECT_QUANTITY = "correct_quantity"
    RESOLVE_EXCEPTION = "resolve_exception"
    MAP_CATALOGUE = "map_catalogue"
    SET_RATE = "set_rate"
    APPROVE = "approve"
    REJECT = "reject"
    EXPORT = "export"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"
    JSON_SIDECAR = "json_sidecar"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
