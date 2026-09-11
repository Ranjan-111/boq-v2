"""AI provider abstraction (T060) — the only seam a model may enter through.

docs/domain-model.md invariant 3: AI may only ever write `*_suggested` /
advisory data. This module defines that boundary for the classification
package: a provider here PROPOSES (kind + prompt in, validated payload out)
and nothing else. The concrete providers are `classification.http_provider`
(real gateway, sync httpx — the analyze job calls from a worker thread) and
`classification.stub_provider` (deterministic, no network, for tests/dev/CI).

Contract points, all machine-checkable:

* `SuggestionSchema` is the value object describing the JSON the model must
  fill (name, description, properties with types, required). Each property
  value is a human description whose FIRST comma-separated segment must be
  a JSON type word (string|number|integer|boolean|array|object|null) — the
  wire schema and the response validator are both derived from that word,
  so an unparsable spec is refused loudly, never guessed.
* `ProviderResult.payload` must validate against the schema it was produced
  under (`validate_payload`), so downstream code never sees a shape the
  caller did not ask for. `validate_payload` is deliberately strict: fields
  outside the schema are refused (undeclared_field), missing required fields
  are refused (missing_required), wrong value types are refused
  (type_mismatch) — never a best-effort parse.
* MANDATORY confidence: a provider response without a parsable confidence
  in [0, 1] raises `AiProviderError` — never a silent default of 1.0 or 0.
  Even `ProviderResult` construction refuses a confidence outside [0, 1],
  so the invariant is carried by the type itself, not by call-site
  discipline. bool is not a confidence; NaN/inf are not confidences.
* Machine-readable failures: every `AiProviderError` carries a short
  snake_case `reason` token (and its message starts with that token) so the
  backend can surface failures honestly in job results without
  string-matching prose. Reasons: invalid_schema, invalid_request,
  network, timeout, http_status, response_too_large, unparsable_json,
  invalid_response, invalid_payload, undeclared_field, missing_required,
  type_mismatch, no_confidence, bad_confidence.

This module imports NO third-party packages and NO project packages — the
import-linter contracts "AI boundary (classification never reaches
engines)" and "Forbidden AI-to-measurement imports" hold trivially here
and are pinned by tests/unit/test_ai_guardrails.py.

Note: `ProviderResult` carries a `provider` name beyond the four fields the
ticket lists (payload/confidence/model/raw_response_id) because the backend
persists prompt_logs with the provider that served each call; the extra
field is additive and mandatory, never defaulted.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# JSON type words a property spec may declare. The FIRST comma-separated
# segment of each property description must be exactly one of these
# (case-insensitive); anything else is an unparsable spec, refused.
JSON_TYPE_WORDS = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})

# Runtime type checks per JSON type word. bool is excluded from number and
# integer on purpose: isinstance(True, int) is True in Python, but a JSON
# `true` is not a number a reviewer could weigh as 1.
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


class AiProviderError(RuntimeError):
    """A provider refused to produce a result — never a silent degradation.

    `reason` is a stable machine-readable snake_case token; the string form
    is always ``"<reason>: <human detail>"`` so log lines stay greppable.
    """

    def __init__(self, detail: str, *, reason: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SuggestionSchema:
    """The JSON shape a model must fill for one suggestion kind.

    `properties` maps field name -> description string whose first
    comma-separated segment is a JSON type word ("string, one of: wall,
    room, ..." declares a string). `required` lists property names that
    must be present in every response. Construction validates: names are
    non-empty, properties non-empty, and required ⊆ properties — a
    malformed schema is a programming error and is refused here, not
    discovered later inside a worker.
    """

    name: str
    description: str
    properties: dict[str, str]
    required: list[str]

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise AiProviderError("schema name must be a non-empty string", reason="invalid_schema")
        if not self.description or not self.description.strip():
            raise AiProviderError(
                f"schema {self.name!r} description must be non-empty", reason="invalid_schema",
            )
        if not self.properties:
            raise AiProviderError(
                f"schema {self.name!r} declares no properties — nothing to suggest",
                reason="invalid_schema",
            )
        unknown_required = [name for name in self.required if name not in self.properties]
        if unknown_required:
            raise AiProviderError(
                f"schema {self.name!r} requires undeclared fields: {unknown_required}",
                reason="invalid_schema",
            )
        for name, spec in self.properties.items():
            _wire_type(name, spec)  # refuses unparsable specs at construction time

    def to_json_schema(self) -> dict[str, Any]:
        """The strict JSON Schema object sent over the wire (structured outputs).

        additionalProperties is false: the response validator refuses any
        field not declared here, so the wire contract and the validator
        cannot drift apart.
        """
        return {
            "type": "object",
            "properties": {
                name: {"type": _wire_type(name, spec), "description": spec}
                for name, spec in self.properties.items()
            },
            "required": list(self.required),
            "additionalProperties": False,
        }


def _wire_type(prop_name: str, spec: str) -> str:
    """The JSON type word of a property spec — refused when unparsable."""
    head = spec.split(",")[0].strip().lower()
    if head not in JSON_TYPE_WORDS:
        raise AiProviderError(
            f"property {prop_name!r} spec {spec!r} does not start with a JSON type word "
            f"({sorted(JSON_TYPE_WORDS)})",
            reason="invalid_schema",
        )
    return head


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """One completed provider call — everything the backend needs to persist.

    `payload` is the model's suggestion, already validated against the
    SuggestionSchema it was requested under (never a best-effort parse).
    `confidence` is mandatory and always in [0, 1] — construction refuses
    anything else, so no code path can hand downstream a default trust
    value. `provider` names the concrete provider ("http"/"stub") for
    prompt-log persistence. `raw_response_id` is the gateway's response id
    when one exists, else None (honest absence, never fabricated).
    """

    payload: dict[str, Any]
    confidence: float
    model: str
    provider: str
    raw_response_id: str | None = None

    def __post_init__(self) -> None:
        validated_confidence(self.confidence)
        if not self.model or not self.model.strip():
            raise AiProviderError("model must be a non-empty string", reason="invalid_schema")


def validated_confidence(raw: Any) -> float:
    """Parse a provider-reported confidence; refuse anything outside [0, 1].

    bool is not a confidence, non-numbers are not confidences, NaN and
    infinity are not confidences. A response that cannot prove its trust
    is a refusal, never a default.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise AiProviderError(
            f"provider confidence is not a number: {raw!r}", reason="bad_confidence",
        )
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise AiProviderError(
            f"provider confidence {value!r} is outside [0, 1]", reason="bad_confidence",
        )
    return value


def validate_payload(schema: SuggestionSchema, payload: Any) -> dict[str, Any]:
    """Refuse any payload that does not satisfy the schema — strictly.

    Called by both providers before a ProviderResult may be built. The
    three refusal reasons (undeclared_field, missing_required,
    type_mismatch) name the exact defect; there is no lenient mode.
    """
    if not isinstance(payload, dict):
        raise AiProviderError(
            f"provider payload is not a JSON object: {type(payload).__name__}",
            reason="invalid_payload",
        )
    for key in payload:
        if key not in schema.properties:
            raise AiProviderError(
                f"payload field {key!r} is not declared in schema {schema.name!r}",
                reason="undeclared_field",
            )
    for name in schema.required:
        if name not in payload:
            raise AiProviderError(
                f"payload is missing required field {name!r} (schema {schema.name!r})",
                reason="missing_required",
            )
    for name, value in payload.items():
        expected = _wire_type(name, schema.properties[name])
        if not _TYPE_CHECKS[expected](value):
            raise AiProviderError(
                f"field {name!r} is {type(value).__name__}, schema {schema.name!r} "
                f"declares {expected}",
                reason="type_mismatch",
            )
    return payload


@runtime_checkable
class AiProvider(Protocol):
    """The one-method seam: propose a suggestion for one kind.

    `kind` names the suggestion kind ("element_classification",
    "exception_explanation", ...), `prompt` carries the evidence text, and
    `schema` pins the response shape. Implementations must return a
    ProviderResult whose payload passes `validate_payload(schema, ...)`
    and whose confidence is present and in [0, 1] — see the module
    docstring for why that is non-negotiable.
    """

    def complete(self, kind: str, prompt: str, schema: SuggestionSchema) -> ProviderResult: ...


__all__ = [
    "JSON_TYPE_WORDS",
    "AiProvider",
    "AiProviderError",
    "ProviderResult",
    "SuggestionSchema",
    "validate_payload",
    "validated_confidence",
]
