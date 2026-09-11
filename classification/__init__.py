"""classification — the AI boundary package (T060/T065).

Providers propose; the sanitizer guards; nothing else happens here. The
package imports NO engine/measurement/persistence code (import-linter
contracts "AI boundary" and "Forbidden AI-to-measurement imports"), no
SQLAlchemy, and no framework — the backend persists what
`classification.provider.ProviderResult` carries; classification itself
never touches a database.
"""
from classification.provider import (
    AiProvider,
    AiProviderError,
    ProviderResult,
    SuggestionSchema,
    validate_payload,
    validated_confidence,
)
from classification.sanitize import (
    QUANTITY_ALIASES,
    AiSanitizeError,
    SanitizedSuggestion,
    sanitize_suggestion,
)

__all__ = [
    "QUANTITY_ALIASES",
    "AiProvider",
    "AiProviderError",
    "AiSanitizeError",
    "ProviderResult",
    "SanitizedSuggestion",
    "SuggestionSchema",
    "sanitize_suggestion",
    "validate_payload",
    "validated_confidence",
]
