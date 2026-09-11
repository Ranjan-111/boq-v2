"""Deterministic provenance sidecar serialization for BOQ exports.

The sidecar is a separate immutable object because the human-facing export
formats cannot carry the complete measurement evidence chain without making
the document unreadable.  It is deliberately a pure serializer: the backend
assembles records from persisted rows and this module only fixes the schema
and byte representation.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def provenance_sidecar_bytes(
    *,
    boq: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    measurements: Sequence[Mapping[str, Any]],
) -> bytes:
    """Serialize the export's complete row → measurement → evidence chain.

    Inputs are copied into a JSON object with sorted keys and compact
    separators.  Callers must provide records in their deterministic BOQ and
    measurement order; no timestamps or other process-local values are added.
    """
    payload = {
        "schema_version": "boq-provenance-v1",
        "boq": dict(boq),
        "rows": [dict(row) for row in rows],
        "measurements": [dict(measurement) for measurement in measurements],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = ["provenance_sidecar_bytes"]
