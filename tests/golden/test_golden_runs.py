"""T121 — golden-run regression suite: byte-identical replay of every fixture.

docs/architecture.md release gate #2 (binding): "Determinism: golden-run
replay byte-identical quantities for all fixtures." docs/testing-strategy.md
§1: "Any diff = test failure, not a 'note'."

What each test enforces:

* ``test_golden_replay_byte_identical`` — parse + measure every committed
  fixture through the REAL pipeline (golden_run.build_document mirrors the
  run-service driving conventions) and compare the canonical serialization
  BYTE-EXACTLY against the committed golden (string equality of canonical
  forms — never dict ==, so formatting noise cannot hide drift).
* ``test_replay_determinism_same_process`` — run every fixture twice inside
  one process; the two replays must be byte-identical (architecture gate #2's
  in-process half).
* ``test_golden_files_exactly_cover_the_registry`` — no golden may silently
  appear or disappear: the committed files and the driver registry must be
  the same set. A new fixture without a golden fails HERE with the
  regeneration instruction, not by being skipped.
* ``test_goldens_record_schema_and_engine_version`` — the version contract:
  every golden records its engine_version stamp (docs/domain-model.md — old
  runs replay by their stamped version; the golden must state which one it
  replays under).

Drift doctrine (docs/testing-strategy.md §8): a mismatch while the golden's
recorded engine_version equals the current ENGINE_VERSION is UNINTENDED
drift — behavior moved under the same version label, which breaks replay of
every persisted run. A deliberate behavior change bumps ENGINE_VERSION FIRST
(takeoff/rules/__init__.py), then regenerates with ``make golden-update``.

Unit-marked: pure engine over committed fixtures, no DB, no I/O beyond
reading the fixture and golden files.
"""
from __future__ import annotations

import difflib
import json

import pytest
from golden_run import (
    DATA_DIR,
    GOLDEN_SCHEMA,
    GoldenCase,
    all_cases,
    build_case_text,
    golden_path,
)

from takeoff.rules import ENGINE_VERSION

CASES: list[GoldenCase] = all_cases()
CASE_IDS: list[str] = [c.case_id for c in CASES]


def _version_of(doc: dict[str, object]) -> str:
    version = doc.get("engine_version") or doc.get("current_engine_version")
    return str(version) if version else "<missing>"


def _drift_message(case: GoldenCase, golden: str, live: str) -> str:
    """The loud, actionable failure: version contract + deliberate path."""
    try:
        golden_doc = json.loads(golden)
    except json.JSONDecodeError:
        return (
            f"GOLDEN-RUN DRIFT: {case.fixture} (sheet {case.sheet_id}) — the "
            f"committed golden {golden_path(case).name} is not valid JSON. "
            "Never hand-edit goldens; regenerate with `make golden-update`."
        )
    golden_version = _version_of(golden_doc)
    diff_lines = list(difflib.unified_diff(
        golden.splitlines(), live.splitlines(),
        fromfile=f"golden/{golden_path(case).name}", tofile="replay", n=1, lineterm="",
    ))[:30]
    head = (
        f"GOLDEN-RUN DRIFT: {case.fixture} (sheet {case.sheet_id}, case "
        f"{case.case_id}) — replay output differs from the committed golden.\n"
        f"  golden engine_version : {golden_version}\n"
        f"  current ENGINE_VERSION: {ENGINE_VERSION}\n"
    )
    if golden_version == ENGINE_VERSION:
        verdict = (
            "The recorded engine_version EQUALS the current ENGINE_VERSION, so this "
            "drift is UNINTENDED: engine behavior changed without a version bump. "
            "Persisted runs stamp this version and replay by it (docs/domain-model.md "
            "replay contract: rule_id + engine_version + inputs_digest) — silent "
            "behavior drift at the same version breaks that promise.\n"
            "If this change is deliberate, bump ENGINE_VERSION "
            "(takeoff/rules/__init__.py) first, then run `make golden-update`."
        )
    else:
        verdict = (
            f"The committed golden still records engine_version {golden_version} while "
            f"the engine is at {ENGINE_VERSION}: the version moved but the goldens were "
            "not regenerated. If the bump is deliberate, run `make golden-update` "
            "(the bump must come FIRST — old runs keep replaying by their stamped "
            "version; the golden doc records which version it replays under)."
        )
    excerpt = "\n".join(diff_lines)
    return f"{head}\n{verdict}\n\nfirst differing lines:\n{excerpt}"


@pytest.mark.unit
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_golden_replay_byte_identical(case: GoldenCase) -> None:
    live = build_case_text(case)
    path = golden_path(case)
    if not path.exists():
        pytest.fail(
            f"GOLDEN-RUN MISSING: no committed golden for {case.fixture} (sheet "
            f"{case.sheet_id}) — expected {path.name}. Run `make golden-update` "
            "to generate it; never skip a committed fixture (honest refusals are "
            "goldened too, they are not skipped)."
        )
    golden = path.read_text()
    if live != golden:
        pytest.fail(_drift_message(case, golden, live))


@pytest.mark.unit
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_replay_determinism_same_process(case: GoldenCase) -> None:
    """Cross-replay determinism: two independent runs in one process must be
    byte-identical (architecture release gate #2, in-process half)."""
    first = build_case_text(case)
    second = build_case_text(case)
    assert first == second, (
        f"NON-DETERMINISTIC REPLAY: {case.fixture} (sheet {case.sheet_id}) produced "
        "different outputs on two consecutive runs inside one process — the "
        "determinism boundary is broken (no clock, no randomness, no set ordering "
        "may leak into engine output)."
    )


@pytest.mark.unit
def test_golden_files_exactly_cover_the_registry() -> None:
    """No golden may silently appear or disappear: the committed data files
    and the driver registry must be the same set."""
    on_disk = {p.stem for p in DATA_DIR.glob("*.json")}
    assert on_disk == set(CASE_IDS), (
        f"golden/data mismatch — extra on disk: {sorted(on_disk - set(CASE_IDS))}, "
        f"missing on disk: {sorted(set(CASE_IDS) - on_disk)}. "
        "Run `make golden-update` after changing the fixture set."
    )


@pytest.mark.unit
def test_goldens_record_schema_and_engine_version() -> None:
    """The version contract: every golden states which engine version it
    replays under (docs/domain-model.md — old runs replay by stamped version)."""
    for case in CASES:
        path = golden_path(case)
        assert path.exists(), f"missing golden: {path.name} (run `make golden-update`)"
        doc = json.loads(path.read_text())
        assert doc["schema"] == GOLDEN_SCHEMA, f"{case.case_id}: unexpected schema"
        assert _version_of(doc) not in ("", "<missing>"), (
            f"{case.case_id}: golden must record its engine_version"
        )


@pytest.mark.unit
def test_every_committed_fixture_is_covered() -> None:
    """Guard the registry against fixtures sneaking in uncovered: every
    committed fixture file under tests/fixtures/{dxf,pdf,raster} must appear
    in at least one golden case (parse-refused ones included — their refusal
    is goldened, never skipped)."""
    fixtures_root = DATA_DIR.parent.parent / "fixtures"
    committed = {
        f"{kind}/{p.name}"
        for kind in ("dxf", "pdf", "raster")
        for p in (fixtures_root / kind).iterdir()
        if not p.name.startswith(".")
    }
    covered = {c.fixture for c in CASES}
    assert committed == covered, (
        f"fixture/registry drift — uncovered fixtures: {sorted(committed - covered)}, "
        f"registry entries without a fixture: {sorted(covered - committed)}. "
        "Every committed fixture gets a golden case (honest refusals included); "
        "add it to golden_run._registry and run `make golden-update`."
    )
