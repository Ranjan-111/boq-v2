"""AI analyze service (Round 6, T060/T061/T062 slice) — advisory only.

The analyze pass is the AI layer's ONLY write path, and it writes ONLY
advisory rows (docs/domain-model.md invariant 3):
  * prompt_logs — every provider call, verbatim, persisted BEFORE any
    suggestion is derived from it;
  * ai_suggestions — sanitized proposals with mandatory confidence;
  * elements.ai_* columns are NOT touched by this service in V1 (the
    deterministic type_source stays authoritative; human overrides land
    through the review API, Worker B's T073).

Nothing here can change a quantity, a state, or a BOQ row. A provider
failure fails the analyze job honestly — it never fabricates a
suggestion, and an empty run produces zero suggestions, not filler.

The suggestions this pass produces in V1 (classification kinds):
  * element_classification — for every element of the run: a proposed
    element_type + label reading from the element's own label + geometry
    type (advisory; the deterministic engine's type stays untouched);
  * exception_explanation — for every unresolved exception: a
    plain-language explanation citing the evidence refs.
Both kinds are advisory suggestions only. Confidence comes from the
provider (mandatory) and is clamped to [0, 1] at insert by the DB check
constraint; the service refuses out-of-range provider confidences
BEFORE insert (a clean AiProviderError surfaces in the job result).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import (
    AiSuggestion,
    Element,
    ExceptionModel,
    MeasurementRun,
    PromptLogModel,
)

# Confidence ceiling for advisory suggestions: even a perfect model's
# element-type guess is a suggestion, never a determination. The scale
# confirmation doctrine caps harder (0.90) because a wrong scale poisons
# every quantity; a wrong label poisons one line a human reviews.
MAX_SUGGESTION_CONFIDENCE = 0.95


class AnalyzeError(RuntimeError):
    """The analyze pass cannot run — honest failure, never a faked result."""


def _build_provider() -> Any:
    """Provider from settings. classification stays import-light."""
    from classification.http_provider import HttpProvider
    from classification.stub_provider import StubProvider

    settings = get_settings()
    if settings.ai_provider == "http":
        return HttpProvider(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
    if settings.ai_provider != "stub":
        raise AnalyzeError(f"unknown AI provider {settings.ai_provider!r}")
    return StubProvider(model=settings.ai_model)


async def execute_analyze(
    session: AsyncSession, *, run_id: str,
) -> dict[str, Any]:
    """Job body for kind=ai_analyze: propose, sanitize, persist advisory rows.

    Idempotency: a second analyze of the same run replaces that run's
    suggestion rows (the old proposals are invalidated by the new pass —
    same doctrine as re-parse replacing sheets). Prompt logs are
    append-only history and are never deleted.
    """
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        raise AnalyzeError(f"run {run_id} not found")
    if run.status not in ("completed", "completed_with_exceptions"):
        raise AnalyzeError(
            f"run {run.status!r} has nothing to analyze — wait for completion")

    provider = _build_provider()
    elements = (await session.execute(
        select(Element).where(Element.run_id == run.id)
        .order_by(Element.id)
    )).scalars().all()
    exceptions = (await session.execute(
        select(ExceptionModel).where(
            ExceptionModel.run_id == run.id,
            ExceptionModel.resolved_at.is_(None),
        ).order_by(ExceptionModel.created_at, ExceptionModel.id)
    )).scalars().all()

    from classification.provider import SuggestionSchema

    # Fresh pass replaces this run's prior suggestions (invalidate-by-replace).
    old = (await session.execute(
        select(AiSuggestion).where(AiSuggestion.run_id == run.id)
    )).scalars().all()
    for row in old:
        await session.delete(row)
        await session.flush()

    written = 0
    prompt_ids: list[str] = []
    for element in elements:
        schema = SuggestionSchema(
            name="element_classification",
            description=(
                "Propose an element_type and read a display label for one "
                "building element. Advisory only — never a quantity."),
            properties={
                "element_type": "string, one of: wall, room, slab, door, "
                                "window, opening, floor_finish, other",
                "label": "string, short human-readable label",
                "rationale": "string, one-sentence why",
            },
            required=["element_type", "label", "rationale"],
        )
        prompt = (
            f"Element id={element.id} label={element.label!r} "
            f"current_type={element.element_type} "
            f"geometry={(element.element_type and element.label) or ''}".strip()
        )
        result = _call_provider(provider, "element_classification", prompt,
                                schema)
        prompt_log = PromptLogModel(
            id=str(uuid.uuid4()), provider=result.provider,
            model=result.model, kind="element_classification",
            prompt=prompt, response=result.payload,
        )
        session.add(prompt_log)
        await session.flush()
        prompt_ids.append(str(prompt_log.id))

        from classification.sanitize import sanitize_suggestion

        sanitized = sanitize_suggestion("element_classification",
                                        result.payload)
        suggestion = AiSuggestion(
            id=str(uuid.uuid4()), run_id=run.id,
            subject_type="element", subject_id=uuid.UUID(str(element.id)),
            suggestion_type="element_classification",
            payload={**sanitized.payload,
                     "rejected_fields": sanitized.rejected_fields},
            confidence=_clamped_confidence(result.confidence),
            model=result.model,
            prompt_log_id=uuid.UUID(prompt_log.id),
        )
        session.add(suggestion)
        await session.flush()
        written += 1

    for exc in exceptions:
        schema = SuggestionSchema(
            name="exception_explanation",
            description=(
                "Explain one measurement exception in plain language, "
                "citing the evidence. Advisory only — never a quantity."),
            properties={
                "explanation": "string, 1-3 sentences, cite the evidence",
                "suggested_action": "string, what a reviewer should do",
            },
            required=["explanation", "suggested_action"],
        )
        prompt = (f"Exception code={exc.code} severity={exc.severity} "
                  f"message={exc.message!r}")
        result = _call_provider(provider, "exception_explanation", prompt,
                                schema)
        prompt_log = PromptLogModel(
            id=str(uuid.uuid4()), provider=result.provider,
            model=result.model, kind="exception_explanation",
            prompt=prompt, response=result.payload,
        )
        session.add(prompt_log)
        await session.flush()
        prompt_ids.append(str(prompt_log.id))

        from classification.sanitize import sanitize_suggestion

        sanitized = sanitize_suggestion("exception_explanation",
                                        result.payload)
        session.add(AiSuggestion(
            id=str(uuid.uuid4()), run_id=run.id,
            subject_type="exception", subject_id=uuid.UUID(str(exc.id)),
            suggestion_type="exception_explanation",
            payload={**sanitized.payload,
                     "rejected_fields": sanitized.rejected_fields},
            confidence=_clamped_confidence(result.confidence),
            model=result.model,
            prompt_log_id=uuid.UUID(prompt_log.id),
        ))
        await session.flush()
        written += 1

    return {
        "ok": True,
        "suggestions_written": written,
        "prompt_logs": len(prompt_ids),
        "elements_seen": len(elements),
        "exceptions_seen": len(exceptions),
    }


def _call_provider(provider: Any, kind: str, prompt: str,
                   schema: Any) -> Any:
    """One provider call with an honest failure envelope.

    The classification AiProviderError is translated to AnalyzeError so
    the job result carries a machine-readable reason (the analyze pass
    fails; it never degrades to a faked suggestion).
    """
    from classification.provider import AiProviderError

    try:
        return provider.complete(kind, prompt, schema)
    except AiProviderError as exc:
        raise AnalyzeError(f"provider call failed: {exc}") from exc


def _clamped_confidence(confidence: float) -> float:
    """Refuse out-of-range confidences BEFORE insert.

    The DB constraint is the backstop; the service refuses loudly instead
    of silently coercing — a provider that returns 1.7 is broken, not
    enthusiastic.
    """
    if not 0.0 <= confidence <= MAX_SUGGESTION_CONFIDENCE:
        raise AnalyzeError(
            f"provider confidence {confidence!r} outside [0, {MAX_SUGGESTION_CONFIDENCE}]"
        )
    return round(float(confidence), 3)


async def load_insights(session: AsyncSession, *, run_id: str) -> dict[str, Any]:
    """GET /runs/{id}/ai/insights — the advisory rows, read-only."""
    run = (await session.execute(
        select(MeasurementRun).where(MeasurementRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        raise AnalyzeError(f"run {run_id} not found")
    suggestions = (await session.execute(
        select(AiSuggestion).where(AiSuggestion.run_id == run.id)
        .order_by(AiSuggestion.created_at, AiSuggestion.id)
    )).scalars().all()
    return {
        "run_id": str(run.id),
        "generated_note": "advisory only — nothing here changes a quantity",
        "suggestions": [
            {
                "id": str(s.id),
                "subject_type": s.subject_type,
                "subject_id": str(s.subject_id),
                "suggestion_type": s.suggestion_type,
                "payload": s.payload,
                "confidence": float(s.confidence),
                "model": s.model,
                "accepted": s.accepted,
                "created_at": (s.created_at.isoformat()
                               if isinstance(s.created_at, datetime)
                               else None),
            }
            for s in suggestions
        ],
    }
