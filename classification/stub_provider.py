"""Stub AI provider (T060) — deterministic canned payloads, no network.

For tests, dev, and CI: `StubProvider` makes no HTTP call, needs no API
key, and is fully deterministic (same kind + schema in, byte-identical
ProviderResult out — the prompt never influences output, so there is no
injection surface at all).

Honesty constraints, all deliberate:

* Only the two kinds the product persists today have canned payloads
  ("element_classification", "exception_explanation", matching the schemas
  backend/app/services/ai_service.py builds). An unknown kind is REFUSED
  (reason=unknown_kind) — a stub that invents a shape for a kind it was
  never taught would be exactly the silent fabrication this codebase
  refuses.
* The canned payload is validated against the CALLER'S schema before it is
  returned (the same `validate_payload` the HTTP provider applies to real
  responses), so schema drift surfaces as a loud refusal here, in the
  caller, not as a stored suggestion with the wrong shape.
* The stub confidence is a declared 0.05: a stub evaluates nothing, so its
  proposals sit at the bottom of the trust range — visible as
  needs-review, never mistaken for model evidence. raw_response_id is a
  deterministic "stub-<kind>" string: honest about being synthetic, still
  correlatable in prompt_logs.
"""
from __future__ import annotations

from classification.provider import (
    AiProvider,
    AiProviderError,
    ProviderResult,
    SuggestionSchema,
    validate_payload,
    validated_confidence,
)

# Declared, not computed: a stub has no evidence, so its confidence is the
# floor of the advisory range (and comfortably inside the [0, 0.95] clamp
# the backend applies before insert).
STUB_CONFIDENCE = 0.05

# Canned payloads keyed by suggestion kind. Field sets mirror the schemas
# ai_service.py constructs; validate_payload refuses any drift.
_CANNED: dict[str, dict[str, str]] = {
    "element_classification": {
        "element_type": "other",
        "label": "(stub) unclassified element",
        "rationale": "deterministic stub proposal — no model ran, nothing was evaluated",
    },
    "exception_explanation": {
        "explanation": "(stub) no model ran; review this exception against its cited evidence",
        "suggested_action": "inspect the evidence refs and resolve or accept the exception",
    },
}


class StubProvider(AiProvider):
    """Returns the canned payload for a known kind, refusing everything else."""

    def __init__(self, model: str = "advisory-default") -> None:
        self._model = model

    def complete(self, kind: str, prompt: str, schema: SuggestionSchema) -> ProviderResult:
        if kind not in _CANNED:
            raise AiProviderError(
                f"stub provider has no canned payload for kind {kind!r} "
                f"(known kinds: {sorted(_CANNED)})",
                reason="unknown_kind",
            )
        payload: dict[str, str] = dict(_CANNED[kind])
        validate_payload(schema, payload)
        return ProviderResult(
            payload=dict(payload),
            confidence=validated_confidence(STUB_CONFIDENCE),
            model=self._model,
            provider="stub",
            raw_response_id=f"stub-{kind}",
        )


__all__ = ["STUB_CONFIDENCE", "StubProvider"]
