"""Architecture guard regressions — the import contracts must actually bite.

import-linter's 9 contracts (pyproject.toml [tool.importlinter]) encode the
documented dependency graph (docs/architecture.md §E). A contract that passes
on a clean tree proves nothing — this suite proves a REAL violation fails the
real guard, in an isolated copy so the user's tree is never mutated.

Also pins the rule-registry discipline: the engine must measure through the
registered rule callable (run_rule), never a private bypass, so replay is
bound to a versioned rule.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from takeoff import rules

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PACKAGES = ("core", "ingestion", "takeoff", "classification",
                   "provenance", "review", "boq", "catalog", "pricing", "exports")


def _run_import_linter(cwd: Path) -> subprocess.CompletedProcess[str]:
    lint_imports = ROOT / ".venv" / "bin" / "lint-imports"
    return subprocess.run(
        [str(lint_imports), "--config", "pyproject.toml", "--no-cache"],
        capture_output=True, text=True, cwd=cwd, check=False,
    )


@pytest.fixture
def isolated_tree(tmp_path: Path) -> Path:
    """A disposable copy of the source tree + guard config, safe to violate."""
    tree = tmp_path / "tree"
    tree.mkdir()
    for name in (*SOURCE_PACKAGES, "pyproject.toml"):
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, tree / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, tree / name)
    return tree


@pytest.mark.guard
def test_contracts_pass_on_clean_tree(isolated_tree: Path) -> None:
    result = _run_import_linter(isolated_tree)
    assert result.returncode == 0, f"clean tree must keep all contracts:\n{result.stdout}"


VIOLATIONS = [
    # (package, contract fragment that must appear in the failure output)
    ("boq", "BOQ never reaches takeoff"),
    ("exports", "Exports never reach engines"),
    ("classification", "Forbidden AI-to-measurement imports"),
]


@pytest.mark.guard
@pytest.mark.parametrize(("package", "contract"), VIOLATIONS)
def test_contract_detects_real_violation(
    isolated_tree: Path, package: str, contract: str
) -> None:
    # Inject a forbidden import into a real module of the isolated copy.
    target = isolated_tree / package / "__init__.py"
    target.write_text(
        target.read_text(encoding="utf-8") + f"\nimport takeoff  # guard probe: {package}\n",
        encoding="utf-8",
    )
    result = _run_import_linter(isolated_tree)
    assert result.returncode != 0, f"{package} -> takeoff must break a contract"
    assert contract in result.stdout, (
        f"violation must be attributed to {contract!r}:\n{result.stdout}"
    )


@pytest.mark.guard
def test_core_importing_third_party_is_refused(isolated_tree: Path) -> None:
    probe = isolated_tree / "core" / "guard_probe.py"
    probe.write_text("import sqlalchemy  # guard probe\n", encoding="utf-8")
    (isolated_tree / "core" / "__init__.py").write_text(
        (isolated_tree / "core" / "__init__.py").read_text(encoding="utf-8")
        + "\nfrom core import guard_probe  # noqa: F401\n",
        encoding="utf-8",
    )
    result = _run_import_linter(isolated_tree)
    assert result.returncode != 0, "core must not import third-party packages"
    assert "Core imports no third-party packages" in result.stdout, (
        f"violation must be attributed to the core contract:\n{result.stdout}"
    )


@pytest.mark.guard
def test_engine_measures_through_registered_rule_callable() -> None:
    """The engine never bypasses the versioned rule registry (replay discipline)."""
    # The engine module must reference the registry's run_rule, not private fns.
    engine_source = (ROOT / "takeoff" / "engine.py").read_text(encoding="utf-8")
    assert "run_rule(" in engine_source, "engine must measure via run_rule (registry)"
    # The rules used by the engine are exactly registered rules.
    registered = {r.rule_id for r in rules.all_rules()}
    assert "wall.centerline.length.v1" in registered
    assert "wall.footprint.area.v1" in registered
    # Running an unregistered rule id must refuse, not guess.
    with pytest.raises(ValueError, match="unknown rule"):
        rules.run_rule("wall.centerline.length.v9", [])


@pytest.mark.guard
def test_registry_rejects_duplicate_rule_ids() -> None:
    with pytest.raises(ValueError, match="duplicate rule id"):
        @rules.register("wall.centerline.length.v1", quantity_type="length",
                        description="probe")
        def _probe(inputs: list) -> float:  # pragma: no cover - never called
            return 0.0


@pytest.mark.guard
def test_registry_import_is_idempotent() -> None:
    """Re-importing the registry module keeps one registration per rule."""
    module = importlib.reload(rules)
    assert len([r for r in module.all_rules() if r.rule_id == "wall.centerline.length.v1"]) == 1
