"""Typed identifiers for the boq-v2 domain.

Every entity id is a distinct newtype so that ids can never be silently
cross-assigned (a RunId is not a ProjectId). UUIDv7 preferred for sortability;
validation happens here, once, at the boundary.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


def new_id() -> str:
    """Mint a UUIDv7 (time-ordered); falls back to v4 on old stdlibs."""
    try:
        return str(uuid.uuid7())
    except AttributeError:  # pragma: no cover - depends on Python version
        return str(uuid.uuid4())


def _validate(value: str, label: str) -> str:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid {label}: {value!r}") from exc
    return value


@dataclass(frozen=True, slots=True)
class ProjectId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "ProjectId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StoreyId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "StoreyId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DrawingFileId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "DrawingFileId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SheetId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "SheetId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RunId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "RunId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ElementId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "ElementId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class GeometryId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "GeometryId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MeasurementId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "MeasurementId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ExceptionId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "ExceptionId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CatalogueItemId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "CatalogueItemId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BoqId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "BoqId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BoqItemId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "BoqItemId")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ExportId:
    value: str

    def __post_init__(self) -> None:
        _validate(self.value, "ExportId")

    def __str__(self) -> str:
        return self.value
