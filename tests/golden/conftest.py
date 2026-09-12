"""T121 — golden-run suite package: expose the driver as a sibling module so
test_golden_runs.py and `python tests/golden/golden_run.py` share ONE code
path (the serializer can never diverge between test and regeneration)."""
from __future__ import annotations

import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))
