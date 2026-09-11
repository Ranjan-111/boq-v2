"""T065/T122 — AI boundary guardrails: sanitize, refuse, never default trust.

The doctrines under test:

  * sanitize_suggestion strips EVERY quantity alias at EVERY nesting depth
    (dict-in-dict, list-in-dict, dict-in-list) and records the dotted path
    in rejected_fields — the sanitized payload can never contain a key
    from QUANTITY_ALIASES anywhere, so downstream code physically cannot
    read a typed quantity out of a suggestion.
  * A hallucinated number in PROSE still flows (description: "wall is
    5.3m" is evidence text) — the guardrail is about TYPED quantity
    fields, not censoring words — but embedded JSON in strings is never
    re-parsed (no smuggling a {"area": 999} through a string).
  * Providers without a parsable confidence in [0, 1] raise: HttpProvider
    with MockTransport returning no confidence, a malformed-JSON body, a
    non-numeric confidence, out-of-range confidences — and never a silent
    1.0/0 default. ProviderResult construction itself refuses out-of-range
    confidence (the type carries the invariant).
  * Schema-invalid provider responses are REFUSED: extra/undeclared
    fields, missing required fields, wrong types — never a best-effort
    parse. The wire request actually carries the JSON schema
    (structured-outputs contract) — asserted against the MockTransport
    request capture.
  * StubProvider is deterministic (two calls byte-identical) and refuses
    unknown kinds rather than inventing a shape.
  * The classification package can never import takeoff/ingestion/boq/
    pricing: a forbidden import injected into an isolated copy of the
    tree must FAIL the real import-linter contracts ("Forbidden AI-to-
    measurement imports" and "AI boundary"), and the pyproject contract
    entries must exist and stay intact.

NO network is ever touched: HttpProvider tests run on httpx.MockTransport.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from classification.http_provider import HttpProvider
from classification.provider import (
    AiProviderError,
    ProviderResult,
    SuggestionSchema,
    validate_payload,
    validated_confidence,
)
from classification.sanitize import (
    MAX_STRING_CHARS,
    QUANTITY_ALIASES,
    sanitize_suggestion,
)
from classification.stub_provider import StubProvider

ROOT = Path(__file__).resolve().parents[2]

# The schema backend/app/services/ai_service.py builds for the analyze
# pass — pinned here so the sanitizer is tested against the real shape.
ELEMENT_SCHEMA = SuggestionSchema(
    name="element_classification",
    description="Propose an element_type and read a display label.",
    properties={
        "element_type": (
            "string, one of: wall, room, slab, door, window, opening, floor_finish, other"
        ),
        "label": "string, short human-readable label",
        "rationale": "string, one-sentence why",
    },
    required=["element_type", "label", "rationale"],
)


def _no_aliases_anywhere(node: object) -> bool:
    """True when NO dict key anywhere equals a quantity alias."""
    if isinstance(node, dict):
        for key in node:
            if str(key).strip().lower() in QUANTITY_ALIASES:
                return False
            if not _no_aliases_anywhere(node[key]):
                return False
        return True
    if isinstance(node, (list, tuple)):
        return all(_no_aliases_anywhere(item) for item in node)
    return True


@pytest.mark.unit
class TestSanitizeStripsQuantityAliases:
    def test_top_level_alias_set_is_exact_and_closed(self) -> None:
        # T065 names the aliases; the sanitizer must carry exactly that set.
        assert QUANTITY_ALIASES == frozenset({
            "value", "quantity", "area", "length", "count", "volume",
            "measurement", "qty", "quantity_m2", "length_m", "num", "number",
        })

    def test_strips_every_alias_at_top_level(self) -> None:
        payload = {alias: 1 for alias in QUANTITY_ALIASES}
        payload["element_type"] = "wall"
        result = sanitize_suggestion("element_classification", payload)
        assert result.payload == {"element_type": "wall"}
        assert set(result.rejected_fields) == set(QUANTITY_ALIASES)

    def test_strips_aliases_nested_in_dicts(self) -> None:
        payload = {
            "element_type": "room",
            "evidence": {"area": 42.5, "length": "12", "note": "hand sketch"},
        }
        result = sanitize_suggestion("element_classification", payload)
        assert result.payload == {
            "element_type": "room",
            "evidence": {"note": "hand sketch"},
        }
        assert result.rejected_fields == ("evidence.area", "evidence.length")

    def test_strips_aliases_inside_lists_of_dicts(self) -> None:
        payload = {
            "element_type": "wall",
            "openings": [
                {"name": "door D1", "count": 2, "width_ok": "yes"},
                {"name": "window W1", "qty": 5},
            ],
        }
        result = sanitize_suggestion("element_classification", payload)
        assert result.payload["openings"] == [
            {"name": "door D1", "width_ok": "yes"},
            {"name": "window W1"},
        ]
        assert result.rejected_fields == ("openings[0].count", "openings[1].qty")

    def test_strips_aliases_deep_mixed_nesting(self) -> None:
        payload = {
            "a": {"b": [{"c": {"number": 3, "keep": "text"}}]},
            "top": 7,  # unknown non-alias keys pass (allow-by-default)
        }
        result = sanitize_suggestion("k", payload)
        assert result.payload == {
            "a": {"b": [{"c": {"keep": "text"}}]},
            "top": 7,
        }
        assert result.rejected_fields == ("a.b[0].c.number",)

    def test_alias_match_is_lowercased_and_trimmed(self) -> None:
        result = sanitize_suggestion("k", {"Area": 1, " LENGTH ": 2, "keep": "x"})
        assert result.payload == {"keep": "x"}
        assert set(result.rejected_fields) == {"Area", " LENGTH "}

    def test_sanitized_payload_never_contains_an_alias_key(self) -> None:
        # The load-bearing property, checked structurally: after sanitize,
        # no dict key anywhere is a quantity alias.
        payload = {
            "element_type": "wall",
            "nested": {"area": 1, "deeper": [{"qty": 1}, {"value": 2}]},
        }
        result = sanitize_suggestion("element_classification", payload)
        assert _no_aliases_anywhere(result.payload)


@pytest.mark.unit
class TestSanitizeProseVsTyped:
    def test_hallucinated_number_in_prose_flows_as_text(self) -> None:
        # The guardrail is about TYPED quantity fields, not prose: an
        # estimator must still read "wall is 5.3m" in a description.
        payload = {
            "element_type": "wall",
            "label": "wall is 5.3m",
            "rationale": "the drawing note says 5.3m — review it",
        }
        result = sanitize_suggestion("element_classification", payload)
        assert result.payload == payload
        assert result.rejected_fields == ()

    def test_prose_numbers_never_become_typed_fields(self) -> None:
        # ...but the sanitized output still contains NO quantity-alias key
        # anywhere — downstream code cannot mistake prose for a number.
        payload = {
            "element_type": "wall",
            "label": "wall is 5.3m",
            "evidence": {"note": "area 12.4 in text"},
        }
        result = sanitize_suggestion("element_classification", payload)
        assert _no_aliases_anywhere(result.payload)
        assert isinstance(result.payload["label"], str)

    def test_embedded_json_in_strings_is_not_reparsed(self) -> None:
        # A string carrying JSON must stay an opaque string: no
        # json.loads anywhere in the sanitizer, so nested JSON cannot
        # smuggle a typed area past the alias strip.
        payload = {
            "element_type": "wall",
            "note": '{"area": 999, "length": 12.5}',
        }
        result = sanitize_suggestion("element_classification", payload)
        assert result.payload["note"] == '{"area": 999, "length": 12.5}'
        assert _no_aliases_anywhere(result.payload)

    def test_input_payload_is_never_mutated(self) -> None:
        payload = {"element_type": "wall", "area": 5}
        original = json.dumps(payload, sort_keys=True)
        sanitize_suggestion("element_classification", payload)
        assert json.dumps(payload, sort_keys=True) == original


@pytest.mark.unit
class TestSanitizeRefusalsAndCaps:
    def test_all_quantity_payload_is_refused_not_emptied(self) -> None:
        from classification.sanitize import AiSanitizeError

        with pytest.raises(AiSanitizeError, match="empty_payload"):
            sanitize_suggestion("k", {"area": 1, "count": 2})

    def test_non_object_payload_refused(self) -> None:
        from classification.sanitize import AiSanitizeError

        with pytest.raises(AiSanitizeError, match="not_an_object"):
            sanitize_suggestion("k", ["area", 1])

    def test_empty_kind_refused(self) -> None:
        from classification.sanitize import AiSanitizeError

        with pytest.raises(AiSanitizeError, match="invalid_kind"):
            sanitize_suggestion("   ", {"x": "y"})

    def test_strings_are_length_capped_and_recorded(self) -> None:
        payload = {"element_type": "wall", "rationale": "r" * (MAX_STRING_CHARS + 10)}
        result = sanitize_suggestion("element_classification", payload)
        assert len(result.payload["rationale"]) == MAX_STRING_CHARS
        assert result.truncated_fields == ("rationale",)
        assert result.rejected_fields == ()

    def test_oversize_payload_refused_not_mangled(self) -> None:
        # A payload that still exceeds 4 KiB after capping is refused —
        # never progressively amputated to fit.
        from classification.sanitize import AiSanitizeError

        payload = {f"note_{i}": "x" * MAX_STRING_CHARS for i in range(12)}
        with pytest.raises(AiSanitizeError, match="payload_too_large"):
            sanitize_suggestion("k", payload)

    def test_depth_bomb_refused(self) -> None:
        from classification.sanitize import AiSanitizeError

        node: dict[str, object] = {"keep": "leaf"}
        for _ in range(12):
            node = {"keep": node}
        with pytest.raises(AiSanitizeError, match="too_deep"):
            sanitize_suggestion("k", node)

    def test_key_order_independence(self) -> None:
        # Determinism: same content, different key order → identical result.
        left = sanitize_suggestion(
            "k", {"b": "one", "a": "two", "area": 1},
        )
        right = sanitize_suggestion(
            "k", {"area": 1, "a": "two", "b": "one"},
        )
        assert left == right


@pytest.mark.unit
class TestSchemaValidation:
    def test_valid_payload_passes(self) -> None:
        payload = {
            "element_type": "wall",
            "label": "kitchen wall",
            "rationale": "thick parallel strokes",
        }
        assert validate_payload(ELEMENT_SCHEMA, payload) == payload

    def test_undeclared_field_refused(self) -> None:
        with pytest.raises(AiProviderError, match="undeclared_field"):
            validate_payload(ELEMENT_SCHEMA, {
                "element_type": "wall", "label": "w", "rationale": "r",
                "confidence_boost": "please",
            })

    def test_missing_required_refused(self) -> None:
        with pytest.raises(AiProviderError, match="missing_required"):
            validate_payload(ELEMENT_SCHEMA, {"element_type": "wall", "label": "w"})

    def test_wrong_type_refused(self) -> None:
        with pytest.raises(AiProviderError, match="type_mismatch"):
            validate_payload(ELEMENT_SCHEMA, {
                "element_type": 7, "label": "w", "rationale": "r",
            })

    def test_non_object_payload_refused(self) -> None:
        with pytest.raises(AiProviderError, match="invalid_payload"):
            validate_payload(ELEMENT_SCHEMA, "wall, definitely")

    def test_schema_construction_refuses_bad_specs(self) -> None:
        with pytest.raises(AiProviderError, match="invalid_schema"):
            SuggestionSchema(
                name="broken", description="d",
                properties={"x": "an unparsable spec"}, required=[],
            )
        with pytest.raises(AiProviderError, match="invalid_schema"):
            SuggestionSchema(
                name="broken", description="d",
                properties={"x": "string, ok"}, required=["undeclared"],
            )

    def test_wire_schema_is_strict_and_carries_the_contract(self) -> None:
        wire = ELEMENT_SCHEMA.to_json_schema()
        assert wire["additionalProperties"] is False
        assert wire["required"] == ["element_type", "label", "rationale"]
        assert wire["properties"]["element_type"]["type"] == "string"


@pytest.mark.unit
class TestConfidenceIsMandatory:
    def _provider(
        self, handler: object,
    ) -> HttpProvider:
        transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
        return HttpProvider(
            base_url="https://ai.test.example", api_key="test-key",
            model="test-model", timeout_seconds=5.0, transport=transport,
        )

    def test_http_response_without_confidence_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "payload": {"element_type": "wall", "label": "w", "rationale": "r"},
            })

        with pytest.raises(AiProviderError) as excinfo:
            self._provider(handler).complete("element_classification", "p", ELEMENT_SCHEMA)
        assert excinfo.value.reason == "no_confidence"

    def test_malformed_json_body_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{not json", headers={})

        provider = self._provider(handler)
        with pytest.raises(AiProviderError) as excinfo:
            provider.complete("element_classification", "p", ELEMENT_SCHEMA)
        assert excinfo.value.reason == "unparsable_json"

    def test_non_numeric_confidence_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "payload": {"element_type": "wall", "label": "w", "rationale": "r"},
                "confidence": "high, probably",
            })

        with pytest.raises(AiProviderError) as excinfo:
            self._provider(handler).complete("element_classification", "p", ELEMENT_SCHEMA)
        assert excinfo.value.reason == "bad_confidence"

    @pytest.mark.parametrize("bad", [1.0001, -0.01])
    def test_out_of_range_confidence_raises(self, bad: float) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "payload": {"element_type": "wall", "label": "w", "rationale": "r"},
                "confidence": bad,
            })

        with pytest.raises(AiProviderError, match="bad_confidence"):
            self._provider(handler).complete("element_classification", "p", ELEMENT_SCHEMA)

    @pytest.mark.parametrize("token", [b"Infinity", b"NaN"])
    def test_non_finite_confidence_token_raises(self, token: bytes) -> None:
        # Non-standard JSON tokens: Python's json accepts them, so a
        # hostile gateway can put Infinity/NaN in the body. The provider's
        # validator must refuse them as non-finite confidences.
        def handler(request: httpx.Request) -> httpx.Response:
            body = b'{"payload": {"element_type": "wall", "label": "w", '
            body += b'"rationale": "r"}, "confidence": ' + token + b"}"
            return httpx.Response(
                200, content=body, headers={"Content-Type": "application/json"},
            )

        with pytest.raises(AiProviderError, match="bad_confidence"):
            self._provider(handler).complete("element_classification", "p", ELEMENT_SCHEMA)

    def test_bool_is_not_a_confidence(self) -> None:
        with pytest.raises(AiProviderError, match="bad_confidence"):
            validated_confidence(True)

    def test_provider_result_construction_refuses_out_of_range(self) -> None:
        # The TYPE carries the invariant: no code path can construct a
        # result with a default or out-of-range trust value.
        with pytest.raises(AiProviderError, match="bad_confidence"):
            ProviderResult(payload={"a": 1}, confidence=1.5, model="m", provider="stub")
        with pytest.raises(AiProviderError, match="bad_confidence"):
            ProviderResult(payload={"a": 1}, confidence=-0.1, model="m", provider="stub")

    def test_valid_confidences_parse(self) -> None:
        assert validated_confidence(0) == 0.0
        assert validated_confidence(1) == 1.0
        assert validated_confidence(0.83) == 0.83


@pytest.mark.unit
class TestHttpProviderContract:
    GOOD_PAYLOAD: ClassVar[dict[str, str]] = {
        "element_type": "wall", "label": "kitchen wall", "rationale": "thick strokes",
    }

    def _capture(self, responses: list[httpx.Response]) -> list[httpx.Request]:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return responses.pop(0)

        return captured  # mutated by the handler; length-checked in tests

    def test_request_sends_the_json_schema(self) -> None:
        # Structured-outputs contract: the wire request carries the schema.
        import json as jsonlib

        def handler(request: httpx.Request) -> httpx.Response:
            body = jsonlib.loads(request.content.decode("utf-8"))
            assert body["response_format"]["type"] == "json_schema", "schema must travel"
            assert (body["response_format"]["json_schema"]["schema"]
                    ["properties"]["element_type"]["type"]) == "string"
            assert body["response_format"]["json_schema"]["strict"] is True
            assert body["kind"] == "element_classification"
            assert body["model"] == "test-model"
            return httpx.Response(200, json={
                "payload": self.GOOD_PAYLOAD, "confidence": 0.9, "id": "resp-1",
            })

        provider = HttpProvider(
            base_url="https://ai.test.example", api_key="test-key",
            model="test-model", timeout_seconds=5.0,
            transport=httpx.MockTransport(handler),
        )
        result = provider.complete("element_classification", "prompt text", ELEMENT_SCHEMA)
        assert result.payload == self.GOOD_PAYLOAD
        assert result.confidence == 0.9
        assert result.provider == "http"
        assert result.raw_response_id == "resp-1"

    def test_schema_invalid_response_refused_extra_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "payload": {**self.GOOD_PAYLOAD, "area": 12.5},
                "confidence": 0.8,
            })

        with pytest.raises(AiProviderError) as excinfo:
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="test-model", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "p", ELEMENT_SCHEMA)
        assert excinfo.value.reason == "undeclared_field"

    def test_schema_invalid_response_refused_wrong_types(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "payload": {**self.GOOD_PAYLOAD, "element_type": ["wall"]},
                "confidence": 0.8,
            })

        with pytest.raises(AiProviderError, match="type_mismatch"):
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="test-model", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "p", ELEMENT_SCHEMA)

    def test_http_error_status_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "overloaded"})

        with pytest.raises(AiProviderError) as excinfo:
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="test-model", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "p", ELEMENT_SCHEMA)
        assert excinfo.value.reason == "http_status"

    def test_oversize_response_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"x" * (1_048_576 + 1), headers={"Content-Type": "application/json"},
            )

        with pytest.raises(AiProviderError, match="response_too_large"):
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="test-model", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "p", ELEMENT_SCHEMA)

    def test_non_object_body_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[self.GOOD_PAYLOAD])

        with pytest.raises(AiProviderError, match="invalid_response"):
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="test-model", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "p", ELEMENT_SCHEMA)

    def test_authorization_sent_with_key_omitted_without(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer test-key"
            return httpx.Response(200, json={
                "payload": self.GOOD_PAYLOAD, "confidence": 0.5,
            })

        def no_key_handler(request: httpx.Request) -> httpx.Response:
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={
                "payload": self.GOOD_PAYLOAD, "confidence": 0.5,
            })

        with_key = HttpProvider(
            base_url="https://ai.test.example", api_key="test-key",
            model="m", transport=httpx.MockTransport(handler),
        )
        with_key.complete("element_classification", "p", ELEMENT_SCHEMA)
        without_key = HttpProvider(
            base_url="https://ai.test.example", api_key="",
            model="m", transport=httpx.MockTransport(no_key_handler),
        )
        without_key.complete("element_classification", "p", ELEMENT_SCHEMA)

    def test_empty_prompt_refused_before_any_network(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not be called")

        with pytest.raises(AiProviderError, match="invalid_request"):
            HttpProvider(
                base_url="https://ai.test.example", api_key="",
                model="m", transport=httpx.MockTransport(handler),
            ).complete("element_classification", "  ", ELEMENT_SCHEMA)


@pytest.mark.unit
class TestStubProvider:
    def test_deterministic_two_calls_identical(self) -> None:
        provider = StubProvider(model="stub-model")
        first = provider.complete(
            "element_classification", "prompt A", ELEMENT_SCHEMA,
        )
        second = provider.complete(
            "element_classification", "a totally different prompt", ELEMENT_SCHEMA,
        )
        assert first == second

    def test_known_kind_returns_validated_payload(self) -> None:
        result = StubProvider().complete("element_classification", "p", ELEMENT_SCHEMA)
        assert result.payload["element_type"] in {
            "wall", "room", "slab", "door", "window", "opening", "floor_finish", "other",
        }
        assert 0.0 < result.confidence < 0.95  # visible, but never trusted as model evidence
        assert result.provider == "stub"
        assert result.raw_response_id == "stub-element_classification"

    def test_unknown_kind_refused(self) -> None:
        other_schema = SuggestionSchema(
            name="scale_proposal", description="Propose a scale. Never a quantity.",
            properties={"factor": "string, ratio text"}, required=["factor"],
        )
        with pytest.raises(AiProviderError) as excinfo:
            StubProvider().complete("scale_proposal", "p", other_schema)
        assert excinfo.value.reason == "unknown_kind"

    def test_stub_payload_drift_against_schema_refused(self) -> None:
        # If ai_service.py's schema ever drops a field the stub emits, the
        # stub refuses loudly rather than returning a wrong-shape payload.
        drifted = SuggestionSchema(
            name="element_classification", description="d",
            properties={"element_type": "string, t"}, required=["element_type"],
        )
        with pytest.raises(AiProviderError, match="undeclared_field"):
            StubProvider().complete("element_classification", "p", drifted)


SOURCE_PACKAGES = (
    "core", "ingestion", "takeoff", "classification",
    "provenance", "review", "boq", "catalog", "pricing", "exports",
)


@pytest.mark.guard
class TestClassificationBoundary:
    """The classification package can never reach engines/measurement code."""

    def _isolated_tree(self, tmp_path: Path) -> Path:
        tree = tmp_path / "tree"
        tree.mkdir(parents=True)
        for name in (*SOURCE_PACKAGES, "pyproject.toml"):
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, tree / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, tree / name)
        return tree

    def _lint(self, cwd: Path) -> subprocess.CompletedProcess[str]:
        lint_imports = Path(sys.executable).with_name("lint-imports")
        return subprocess.run(
            [str(lint_imports), "--config", "pyproject.toml", "--no-cache"],
            capture_output=True, text=True, cwd=cwd, check=False,
        )

    def test_contracts_exist_and_stay_intact(self) -> None:
        # The two AI contracts in pyproject must exist with their teeth.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'name = "AI boundary (classification never reaches engines)"' in text
        assert 'name = "Forbidden AI-to-measurement imports"' in text
        assert "source_modules = [\"classification\"]" in text
        assert ("forbidden_modules = [\"takeoff\", \"ingestion\", \"boq\", \"pricing\"]"
                in text)

    def test_forbidden_import_into_classification_fails_real_linter(self, tmp_path: Path) -> None:
        # Inject a forbidden import into a temp module inside an isolated
        # copy of classification/ — the REAL import-linter must flag it.
        tree = self._isolated_tree(tmp_path)
        probe = tree / "classification" / "guard_probe.py"
        probe.write_text("import takeoff\n", encoding="utf-8")
        (tree / "classification" / "__init__.py").write_text(
            (tree / "classification" / "__init__.py").read_text(encoding="utf-8")
            + "\nfrom classification import guard_probe  # noqa: F401\n",
            encoding="utf-8",
        )
        result = self._lint(tree)
        assert result.returncode != 0, "classification -> takeoff must break contracts"
        assert "Forbidden AI-to-measurement imports" in result.stdout, (
            f"violation must be attributed to the AI contract:\n{result.stdout}"
        )
        assert "AI boundary" in result.stdout, (
            f"the layers contract must also refuse engines from classification:\n{result.stdout}"
        )

    def test_measurement_import_via_each_engine_fails(self, tmp_path: Path) -> None:
        # Each forbidden module individually must trip the contract.
        for forbidden in ("ingestion", "boq", "pricing"):
            tree = self._isolated_tree(tmp_path / forbidden)
            probe = tree / "classification" / f"probe_{forbidden}.py"
            probe.write_text(f"import {forbidden}\n", encoding="utf-8")
            (tree / "classification" / "__init__.py").write_text(
                (tree / "classification" / "__init__.py").read_text(encoding="utf-8")
                + f"\nfrom classification import probe_{forbidden}  # noqa: F401\n",
                encoding="utf-8",
            )
            result = self._lint(tree)
            assert result.returncode != 0, f"classification -> {forbidden} must fail"

    def test_clean_tree_passes(self, tmp_path: Path) -> None:
        tree = self._isolated_tree(tmp_path / "clean")
        result = self._lint(tree)
        assert result.returncode == 0, f"clean tree must keep all contracts:\n{result.stdout}"
