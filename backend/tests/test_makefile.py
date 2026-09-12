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


@pytest.mark.parametrize(
    ("make_target", "proxy_line"),
    [
        # `make api` must serve the SAME port the Vite /api proxy (and e2e)
        # targets — an 8099/8000 drift surfaces in the browser as an opaque
        # 500 (proxy ECONNREFUSED) while the API itself is perfectly healthy.
        ("api", 'target: "http://localhost:8099"'),
    ],
)
def test_make_api_port_matches_vite_proxy(make_target: str, proxy_line: str) -> None:
    repo_root = Path(__file__).resolve().parents[2]

    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    vite = (repo_root / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    # The uvicorn invocation in `make api` binds the expected port.
    assert "--port 8099" in makefile, (
        "`make api` no longer serves :8099 — the Vite dev proxy and the "
        "browser E2E target that port; update frontend/vite.config.ts and "
        "frontend/e2e together, or browsers get proxy ECONNREFUSED 500s."
    )
    # And the Vite proxy still points there.
    assert proxy_line in vite, (
        "frontend/vite.config.ts no longer proxies /api to :8099 — it and "
        "`make api` must agree (see the assertion above)."
    )
