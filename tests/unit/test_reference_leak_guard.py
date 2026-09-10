"""Tests for the reference-leak guard (the guard must not break on an allowlist typo)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tools" / "guards" / "reference_leak.py"


def run_guard() -> int:
    return subprocess.run(
        [sys.executable, str(GUARD)], capture_output=True, text=True, check=False
    ).returncode


def test_guard_passes_on_clean_repo():
    assert run_guard() == 0, "reference-leak guard failed on what should be a clean tree"


def test_guard_detects_leak(tmp_path, monkeypatch):
    # Run the real guard against a disposable repository, never the user's index.
    spec = importlib.util.spec_from_file_location("reference_leak", GUARD)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    bad = tmp_path / "leak.py"
    bad.write_text("TABLE_PREFIX = 'oe_boq'", encoding="utf-8")
    subprocess.run(["git", "add", "--", bad.name], cwd=tmp_path, check=True)
    assert guard.main() == 1, "guard should fail when a leak exists"
    bad.write_text("TABLE_PREFIX = 'boq'", encoding="utf-8")
    assert guard.main() == 0, "guard should pass once the leak is removed"


def test_allowlist_covers_policy_docs():
    text = GUARD.read_text(encoding="utf-8")
    assert "license-analysis.md" in text, "policy doc must stay allowlisted"
