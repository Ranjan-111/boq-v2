#!/usr/bin/env python3
"""Reference-leak guard: fails if OCErp reference material leaks into tracked files.

Policy source: docs/license-analysis.md §4 (binding rules).
Exit 0 = clean; exit 1 = leak detected (list printed to stdout).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Never-allowed strings in tracked source/docs (OCErp-identifying names).
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openconstructionerp", "OCErp package/module name"),
    ("OpenConstructionERP", "OCErp product name"),
    ("oe_", "OCErp module table prefix"),
    ("app/modules/", "OCErp backend module path"),
    ("reference/OpenConstructionERP", "reference repo path in tracked file"),
    ("from app.", "OCErp-style import"),
    ("cad2data", "OCErp DDC converter reference"),
    ("CWICR", "DDC trademark (see license-analysis.md §4.3)"),
)

# Files that are ALLOWED to mention these (policy explanations).
ALLOWLIST: tuple[str, ...] = (
    "docs/license-analysis.md",
    "docs/reuse-matrix.md",
    "docs/architecture.md",
    "docs/ticket-backlog.md",
    "docs/agent-plan.md",
    "docs/reports/",  # reports discuss audits
    "docs/product-vision.md",
    "docs/non-goals.md",
    "reference/README.md",
    ".gitignore",
    "tools/guards/reference_leak.py",  # this file
    "docs/implementation-roadmap.md",
    "docs/domain-model.md",
    "docs/api-contract.md",
    "docs/testing-strategy.md",
    "README.md",
)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["/usr/bin/git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / line for line in out.splitlines() if line.strip()]


def is_allowed(path: Path) -> bool:
    rel = str(path.relative_to(ROOT))
    return any(rel == a or rel.startswith(a) for a in ALLOWLIST)


def main() -> int:
    leaks: list[str] = []
    for path in tracked_files():
        if not path.is_file() or is_allowed(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, why in FORBIDDEN_PATTERNS:
            if pattern in text:
                leaks.append(f"LEAK: {path.relative_to(ROOT)} contains '{pattern}' ({why})")
    if leaks:
        print("\n".join(leaks))
        print(f"\n{len(leaks)} leak(s). See docs/license-analysis.md §4.")
        return 1
    print(f"reference-leak guard: clean ({len(tracked_files())} tracked files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
