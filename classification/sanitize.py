"""Suggestion sanitizer (T065) — the guardrail between AI prose and the product.

docs/domain-model.md invariant 3 keeps AI output out of deterministic data;
this module enforces the narrower, sharper rule for suggestion PAYLOADS:

  **No quantity-like field may ever pass through the AI boundary.**

Policy (all machine-checked, none best-effort):

  * Any payload key whose lowercased name is one of `QUANTITY_ALIASES`
    (value, quantity, area, length, count, volume, measurement, qty,
    quantity_m2, length_m, num, number) is REMOVED at ANY nesting depth —
    inside dicts, inside lists, inside dicts inside lists — and its dotted
    path is recorded in `rejected_fields` (e.g. "walls[0].area"). The set
    is exact-match on the lowercased key: a TYPED quantity field is the
    threat (downstream code reading `payload["area"]` as a number), not
    prose that mentions a number. "wall is 5.3m" in a description field is
    evidence text and flows through untouched — the guardrail is about
    field NAMES a machine would treat as quantities, never about censoring
    the words an estimator needs to read.
  * After stripping, the payload must be non-empty, else `AiSanitizeError`
    (a suggestion that was ONLY quantities is a quantity-smuggling attempt,
    not a suggestion).
  * Strings are length-capped (`MAX_STRING_CHARS`) and the whole payload
    must stay under `MAX_SERIALIZED_BYTES` (4 KiB) serialized — an advisory
    suggestion is a reviewable sentence, not a document. A payload that
    is still too large after capping is REFUSED, never progressively
    mangled: silent truncation-to-fit would let an adversarial model
    dictate what survives.
  * Embedded JSON in strings is NEVER re-parsed. A string value that
    happens to contain '{"area": 999}' stays an opaque string — there is
    no json.loads on any string anywhere in this module, so nested JSON
    cannot smuggle a typed field past the alias strip (no prompt-injection
    via nested JSON).
  * Nesting is depth-capped (`MAX_NESTING_DEPTH`): pathological depth is
    adversarial, not information.

The sanitizer is allow-by-default for non-quantity keys (unknown keys flow
through), but the AI layer never passes UNSANITIZED payloads onward — the
backend persists only `SanitizedSuggestion.payload` plus the rejected-field
record.

Determinism: output payloads are rebuilt in sorted-key order, so two
input dicts with identical content but different key order sanitize to
byte-identical results; rejected_fields/truncated_fields are sorted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# The closed alias set (T065). Exact match on the lowercased key name.
# Additions are deliberate policy changes, never incidental.
QUANTITY_ALIASES = frozenset({
    "value", "quantity", "area", "length", "count", "volume",
    "measurement", "qty", "quantity_m2", "length_m", "num", "number",
})

MAX_STRING_CHARS = 512
MAX_SERIALIZED_BYTES = 4096  # payload must stay STRICTLY UNDER 4 KiB
MAX_NESTING_DEPTH = 8


class AiSanitizeError(ValueError):
    """A payload the AI boundary refuses to pass on — never a silent fix.

    `reason` is a stable snake_case token; the string form is always
    ``"<reason>: <detail>"``.
    """

    def __init__(self, detail: str, *, reason: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SanitizedSuggestion:
    """The only suggestion shape the AI layer may pass onward."""

    kind: str
    payload: dict[str, Any]
    rejected_fields: tuple[str, ...] = ()
    truncated_fields: tuple[str, ...] = field(default=())


def _clean(
    node: Any, depth: int, path: str,
    rejected: list[str], truncated: list[str],
) -> Any:
    """Recursively strip quantity aliases / cap strings; refuses depth bombs.

    Returns the sanitized copy. List order is preserved (order is content);
    dict keys are walked in sorted order (order is not).
    """
    if depth > MAX_NESTING_DEPTH:
        raise AiSanitizeError(
            f"payload nests deeper than {MAX_NESTING_DEPTH} at {path or '<root>'}",
            reason="too_deep",
        )
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key in sorted(node, key=str):
            if str(key).strip().lower() in QUANTITY_ALIASES:
                rejected.append(f"{path}.{key}" if path else str(key))
                continue  # removed and recorded — never passed on
            child = f"{path}.{key}" if path else str(key)
            out[str(key)] = _clean(node[key], depth + 1, child, rejected, truncated)
        return out
    if isinstance(node, list):
        return [
            _clean(item, depth + 1, f"{path}[{index}]", rejected, truncated)
            for index, item in enumerate(node)
        ]
    if isinstance(node, str) and len(node) > MAX_STRING_CHARS:
        # Cap and record; embedded JSON is NOT parsed — the capped string
        # stays an opaque string (see module docstring).
        truncated.append(path)
        return node[:MAX_STRING_CHARS]
    return node


def sanitize_suggestion(kind: str, payload: Any) -> SanitizedSuggestion:
    """Strip quantity-like fields, cap strings, refuse everything dishonest.

    Raises AiSanitizeError for: empty kind, non-object payload, payload
    empty after stripping, payloads still over 4 KiB after capping, and
    nesting deeper than MAX_NESTING_DEPTH. Never mutates the input.
    """
    if not kind or not kind.strip():
        raise AiSanitizeError("kind must be a non-empty string", reason="invalid_kind")
    if not isinstance(payload, dict):
        raise AiSanitizeError(
            f"payload must be a JSON object, got {type(payload).__name__}",
            reason="not_an_object",
        )
    rejected: list[str] = []
    truncated: list[str] = []
    cleaned = _clean(payload, depth=0, path="", rejected=rejected, truncated=truncated)
    # _clean returns a fresh dict for dict input; cast is for the type checker.
    result: dict[str, Any] = dict(cleaned)
    if not result:
        raise AiSanitizeError(
            f"payload for kind {kind!r} is empty after stripping quantity "
            f"fields (rejected: {sorted(rejected)})",
            reason="empty_payload",
        )
    serialized = json.dumps(result, sort_keys=True, ensure_ascii=False)
    if len(serialized.encode("utf-8")) >= MAX_SERIALIZED_BYTES:
        raise AiSanitizeError(
            f"payload for kind {kind!r} serializes to {len(serialized.encode('utf-8'))} "
            f"bytes (cap {MAX_SERIALIZED_BYTES - 1}); refusing rather than mangling",
            reason="payload_too_large",
        )
    return SanitizedSuggestion(
        kind=kind,
        payload=result,
        rejected_fields=tuple(sorted(rejected)),
        truncated_fields=tuple(sorted(truncated)),
    )


__all__ = [
    "MAX_NESTING_DEPTH",
    "MAX_SERIALIZED_BYTES",
    "MAX_STRING_CHARS",
    "QUANTITY_ALIASES",
    "AiSanitizeError",
    "SanitizedSuggestion",
    "sanitize_suggestion",
]
