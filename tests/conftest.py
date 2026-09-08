"""Shared pytest fixtures for the boq-v2 test suite."""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_storage(tmp_path):
    """A local storage dir per test (see backend/app/storage)."""
    d = tmp_path / "storage"
    d.mkdir()
    return d
