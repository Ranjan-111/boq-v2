"""T121 — property-based tests (hypothesis): the replay digest.

docs/domain-model.md §Measurement: computation is replayable as
{rule_id, engine_version, inputs_digest} — "same inputs + rule = same value".
The digest (core.provenance.records.MeasurementInputs.digest, sha256 over a
canonical JSON payload) is the replay verification key, so its own behavior
gets property coverage:

  1. Determinism: the same inputs produce the same digest, every call.
  2. Distinctness-by-construction: any single-token change in the inputs
     (one ref string, one constant value, one constant key added) yields a
     DIFFERENT digest — a replay engine that silently changed an input could
     never pass off the old digest (approximate injectivity; sha256
     collisions are out of scope by design).
  3. Refs are canonicalized as a multiset: the digest sorts refs, so the
     engine's internal ordering of input geometries can never change a
     measurement's identity — a pinned replay-stability contract (the same
     rule inputs presented in a different order replay as the SAME
     measurement, keeping measurement_id stable).

Determinism: the hypothesis profile lives in tests/conftest.py (derandomized
+ fixed max_examples), so CI digests behave identically on every run.
"""
from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from core.provenance.records import MeasurementInputs

# Refs: short hex-ish handle strings (DXF handles / PDF path indices).
REFS = st.lists(
    st.text(alphabet="0123456789ABCDEF", min_size=1, max_size=8),
    min_size=1, max_size=6, unique=True,
)

# Constants: JSON-serializable scalars the engine actually records (scale
# strings, unit codes, numeric tolerances, flags) with text keys.
CONSTANT_KEYS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12)
CONSTANT_VALUES = st.one_of(
    st.integers(min_value=0, max_value=10_000),
    st.booleans(),
    st.text(alphabet="0123456789.", min_size=1, max_size=12),
    st.none(),
)
CONSTANTS = st.dictionaries(CONSTANT_KEYS, CONSTANT_VALUES, max_size=6)


class TestDigestDeterminism:
    @given(refs=REFS, constants=CONSTANTS)
    def test_same_inputs_same_digest_every_call(
        self, refs: list[str], constants: dict[str, object]
    ) -> None:
        inputs = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        assert inputs.digest() == inputs.digest()
        twin = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        assert inputs.digest() == twin.digest()


class TestDigestDistinctness:
    """Any single-token change in the inputs changes the digest — the replay
    key is injective on distinct-by-construction inputs."""

    @given(refs=REFS, constants=CONSTANTS)
    def test_single_ref_change_alters_digest(
        self, refs: list[str], constants: dict[str, object]
    ) -> None:
        base = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        assume_new = list(refs)
        assume_new[0] = assume_new[0] + "FF"  # distinct-by-construction
        changed = MeasurementInputs(refs=tuple(assume_new), constants=dict(constants))
        assert base.digest() != changed.digest()

    @given(refs=REFS, constants=CONSTANTS, key=CONSTANT_KEYS, value=CONSTANT_VALUES)
    def test_single_constant_overwrite_alters_digest(
        self, refs: list[str], constants: dict[str, object],
        key: str, value: object,
    ) -> None:
        base = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        # overwrite one constant with a distinct-by-construction value
        new_value: object = (
            f"{value!r}|x" if not isinstance(value, str) else value + "|x"
        )
        if constants.get(key) == new_value:
            new_value = f"{new_value}|y"
        mutated = dict(constants)
        mutated[key] = new_value
        changed = MeasurementInputs(refs=tuple(refs), constants=mutated)
        assert base.digest() != changed.digest()

    @given(refs=REFS, constants=CONSTANTS, key=CONSTANT_KEYS, value=CONSTANT_VALUES)
    def test_new_constant_key_alters_digest(
        self, refs: list[str], constants: dict[str, object],
        key: str, value: object,
    ) -> None:
        assume(key not in constants)  # hypothesis shrinks toward the assumption
        base = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        mutated = dict(constants)
        mutated[key] = value
        changed = MeasurementInputs(refs=tuple(refs), constants=mutated)
        assert base.digest() != changed.digest()


class TestDigestRefCanonicalization:
    @given(refs=REFS, constants=CONSTANTS)
    def test_ref_order_never_changes_identity(
        self, refs: list[str], constants: dict[str, object]
    ) -> None:
        """The digest canonicalizes refs as a sorted multiset: the same rule
        inputs presented in a different order are the SAME measurement
        (stable measurement_id across replays — the engine's replay-stability
        contract, pinned here so nobody 'optimizes' the sort away)."""
        forward = MeasurementInputs(refs=tuple(refs), constants=dict(constants))
        shuffled = MeasurementInputs(
            refs=tuple(reversed(refs)), constants=dict(constants)
        )
        assert forward.digest() == shuffled.digest()

    @given(refs=REFS, constants=CONSTANTS)
    def test_digest_is_hex_sha256(
        self, refs: list[str], constants: dict[str, object]
    ) -> None:
        digest = MeasurementInputs(refs=tuple(refs), constants=dict(constants)).digest()
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
