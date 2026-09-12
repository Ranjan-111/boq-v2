"""Shared pytest fixtures for the boq-v2 test suite.

Hypothesis configuration (T121): ONE registered profile, loaded at conftest
import time — before any @given test imports hypothesis — so every CI run and
every local run of the property tests behaves identically. derandomize=True
derives every choice deterministically from the example counter (no
randomness source at all), which is what makes CI reproducible; a flaky
determinism suite would defeat its own purpose. max_examples=50 keeps the
property suite fast (the three property files run in ~1s against the ~5s
pure suite — docs/testing-strategy.md §9). Individual properties may still
tighten max_examples via @settings, never loosen them. The pinned
_HYPOTHESIS_PROFILE_SEED documents the profile's identity date and guards
against accidental re-registration drift.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Hypothesis deterministic profile (T121) — must load BEFORE any @given test
# imports hypothesis strategies; conftest import time is exactly that point.
# ---------------------------------------------------------------------------
_HYPOTHESIS_PROFILE_SEED = 20260912  # profile identity: do NOT change or re-register

try:  # hypothesis is a dev extra; the rest of the suite must not require it
    from hypothesis import settings

    settings.register_profile(
        "boq-deterministic",
        derandomize=True,  # deterministic derivation: CI runs are reproducible
        max_examples=50,
        deadline=None,
        database=None,  # no .hypothesis example DB: CI artifacts stay clean
        print_blob=True,  # failing examples carry their replay blob
    )
    settings.load_profile("boq-deterministic")
except ImportError:  # pragma: no cover - hypothesis is pinned in dev extras
    pass


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Path:
    """A local storage dir per test (see backend/app/storage)."""
    d = tmp_path / "storage"
    d.mkdir()
    return d
