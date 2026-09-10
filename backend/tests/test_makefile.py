"""The documented developer commands must execute and propagate their gates."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
MAKE = shutil.which("make") or "/usr/bin/make"


@pytest.mark.parametrize(
    ("target", "marker"),
    [("test", "not integration"), ("test-integration", "integration"), ("test-all", None)],
)
def test_make_test_targets_include_foundation(
    tmp_path: Path,
    target: str,
    marker: str | None,
) -> None:
    shutil.copyfile("Makefile", tmp_path / "Makefile")
    result = subprocess.run(  # noqa: S603 - fixed make commands
        [MAKE, "-n", target],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "pytest core/tests backend/tests tests" in result.stdout
    if marker is not None:
        assert marker in result.stdout


def test_make_lint_propagates_ruff_failure(tmp_path: Path) -> None:
    shutil.copyfile("Makefile", tmp_path / "Makefile")
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    for name, exit_code in [("ruff", 1), ("mypy", 0)]:
        executable = bin_dir / name
        executable.write_text(f"#!/bin/sh\nexit {exit_code}\n")
        executable.chmod(0o755)
    result = subprocess.run(  # noqa: S603 - fixed make commands
        [MAKE, "lint"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0, "lint swallowed a failing ruff check"
