"""Tests for the reference-leak guard (the guard must not break on an allowlist typo)."""
from __future__ import annotations

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
    # Simulate: tracked file containing forbidden string
    bad = ROOT / "core" / "domain" / "_leak_test.py"
    bad.write_text("TABLE_PREFIX = 'oe_boq'", encoding="utf-8")
    try:
        subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
        rc = run_guard()
        assert rc == 1, "guard should fail when a leak exists"
    finally:
        bad.unlink(missing_ok=True)
        subprocess.run(["git", "reset", "-q"], cwd=ROOT, check=True)


def test_allowlist_covers_policy_docs():
    text = GUARD.read_text(encoding="utf-8")
    assert "license-analysis.md" in text, "policy doc must stay allowlisted"
