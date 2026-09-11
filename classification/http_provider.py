"""HTTP AI provider (T060) — a sync httpx client for the suggestions gateway.

The analyze job calls this from a worker thread, so the client is sync by
design (httpx sync Client; no async anywhere in this module).

Wire contract (this IS the provider-agnostic gateway contract; vendor
adapters would translate it, they do not replace it):

  POST {base_url}/completions          (base_url is the full API root,
                                        e.g. "https://ai.internal.example")
  headers: Authorization: Bearer {api_key}   (omitted when api_key is empty
  body:                                     — a local gateway may be open)
  {
    "kind": "element_classification",
    "prompt": "...",
    "model": "...",
    "response_format": {"type": "json_schema",
                        "json_schema": {"name": ..., "strict": true,
                                        "schema": {strict JSON Schema}}}
  }

  2xx response body (JSON):
  {
    "payload":   {the schema-conforming suggestion object},
    "confidence": 0.0..1.0,            <- MANDATORY, machine-checked
    "model":     "...",                <- optional, echoed when present
    "id":        "..."                 <- optional, persisted as raw_response_id
  }

Anything else is refused with an AiProviderError carrying a machine-readable
reason token: network, timeout, http_status, response_too_large,
unparsable_json, invalid_response, and the payload-validation tokens from
classification.provider. There is no best-effort parse anywhere in this
module: a response that does not satisfy the contract fails the call, the
caller surfaces the refusal, and no suggestion exists.

`transport` is a dependency-injection seam for tests (httpx.MockTransport);
production wiring leaves it None for the default transport. No network is
ever touched by the test suite.
"""
from __future__ import annotations

from typing import Any

import httpx

from classification.provider import (
    AiProvider,
    AiProviderError,
    ProviderResult,
    SuggestionSchema,
    validate_payload,
    validated_confidence,
)

# A suggestion body larger than this is adversarial, not verbose: the
# sanitizer caps stored suggestions at 4 KB, so a megabyte of "suggestion"
# can only be an attempt to smuggle content. Refused before JSON parsing.
MAX_RESPONSE_BYTES = 1_048_576


class HttpProvider(AiProvider):
    """Calls the suggestions gateway over sync httpx; refuses every deviation."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("HttpProvider requires a non-empty base_url (settings ai_base_url)")
        if not timeout_seconds or timeout_seconds <= 0:
            raise ValueError(
                f"HttpProvider timeout_seconds must be positive, got {timeout_seconds!r}"
            )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._model = model
        self._url = f"{base_url.rstrip('/')}/completions"
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds), headers=headers, transport=transport,
        )

    def complete(self, kind: str, prompt: str, schema: SuggestionSchema) -> ProviderResult:
        if not kind or not kind.strip():
            raise AiProviderError("kind must be a non-empty string", reason="invalid_request")
        if not prompt or not prompt.strip():
            raise AiProviderError("prompt must be a non-empty string", reason="invalid_request")
        body = {
            "kind": kind,
            "prompt": prompt,
            "model": self._model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.name, "strict": True,
                                "schema": schema.to_json_schema()},
            },
        }
        try:
            response = self._client.post(self._url, json=body)
        except httpx.TimeoutException as exc:
            raise AiProviderError(
                f"request to {self._url} timed out: {exc}", reason="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise AiProviderError(
                f"{type(exc).__name__} calling {self._url}: {exc}", reason="network",
            ) from exc
        if not 200 <= response.status_code < 300:
            raise AiProviderError(
                f"provider returned HTTP {response.status_code} for kind {kind!r}",
                reason="http_status",
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise AiProviderError(
                f"provider response is {len(response.content)} bytes, over the "
                f"{MAX_RESPONSE_BYTES}-byte cap",
                reason="response_too_large",
            )
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise AiProviderError(
                f"provider response body is not valid JSON: {exc}", reason="unparsable_json",
            ) from exc
        if not isinstance(data, dict):
            raise AiProviderError(
                "provider response body is not a JSON object", reason="invalid_response",
            )
        payload = validate_payload(schema, data.get("payload"))
        if "confidence" not in data:
            raise AiProviderError(
                f"provider response for kind {kind!r} carries no confidence field",
                reason="no_confidence",
            )
        confidence = validated_confidence(data["confidence"])
        raw_model = data.get("model")
        model = raw_model if isinstance(raw_model, str) and raw_model else self._model
        raw_id = data.get("id")
        response_id = raw_id if isinstance(raw_id, str) and raw_id else None
        return ProviderResult(
            payload=payload,
            confidence=confidence,
            model=model,
            provider="http",
            raw_response_id=response_id,
        )

    def close(self) -> None:
        """Release the underlying connection pool (idempotent)."""
        self._client.close()

    def __enter__(self) -> HttpProvider:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


__all__ = ["MAX_RESPONSE_BYTES", "HttpProvider"]
