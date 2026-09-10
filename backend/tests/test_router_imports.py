"""Existing routers can be imported independently of the app entrypoint."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("module", ["backend.app.api.auth", "backend.app.api.projects"])
def test_router_import_does_not_depend_on_entrypoint_order(module: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed module names
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
