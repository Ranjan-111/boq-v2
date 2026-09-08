"""State machines — docs/domain-model.md, enforced as pure functions.

Design: transition tables + one `transition()` entry point that raises on
illegal moves and returns the new state. Callers persist the result; this
module never touches I/O (determinism boundary).
"""
from __future__ import annotations

from typing import Final

from core.domain.enums import (
    BoqStatus,
    MeasurementState,
    RunState,
)

# ---------------------------------------------------------------------------
# Measurement states
# ---------------------------------------------------------------------------

# Initial states a measurement can be minted in by a run.
MEASUREMENT_INITIAL: Final[frozenset[MeasurementState]] = frozenset(
    {
        MeasurementState.MEASURED,
        MeasurementState.MEASURED_ZERO,
        MeasurementState.NEEDS_REVIEW,
        MeasurementState.NOT_MEASURABLE,
        MeasurementState.BLOCKED,
    }
)

# Legal measurement transitions (from -> {to}).
_MEASUREMENT_TRANSITIONS: Final[dict[MeasurementState, frozenset[MeasurementState]]] = {
    MeasurementState.MEASURED: frozenset(
        {MeasurementState.NEEDS_REVIEW, MeasurementState.BLOCKED}
    ),
    # A confirmed zero is a real measured result; it can be re-flagged for
    # review but never silently converted to a nonzero measured value without
    # a correction (which creates a new review record, not a state flip).
    MeasurementState.MEASURED_ZERO: frozenset(
        {MeasurementState.NEEDS_REVIEW, MeasurementState.BLOCKED}
    ),
    MeasurementState.NEEDS_REVIEW: frozenset(
        {
            MeasurementState.MEASURED,  # human accepts
            MeasurementState.MEASURED_ZERO,
            MeasurementState.BLOCKED,
            MeasurementState.NOT_MEASURABLE,
        }
    ),
    MeasurementState.NOT_MEASURABLE: frozenset(
        {MeasurementState.NEEDS_REVIEW, MeasurementState.BLOCKED}
    ),
    MeasurementState.BLOCKED: frozenset(
        {
            MeasurementState.NEEDS_REVIEW,
            MeasurementState.MEASURED,  # blocker resolved + recomputed
            MeasurementState.MEASURED_ZERO,
        }
    ),
}


class IllegalTransition(ValueError):
    """Raised on any state-machine violation. Always a programming error."""

    def __init__(self, machine: str, current: str, target: str) -> None:
        super().__init__(
            f"illegal {machine} transition: {current!r} -> {target!r}"
        )
        self.machine = machine
        self.current = current
        self.target = target


def transition_measurement(
    current: MeasurementState, target: MeasurementState
) -> MeasurementState:
    """Validate + apply a measurement state transition."""
    if target == current:
        return current
    legal = _MEASUREMENT_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise IllegalTransition("measurement", current.value, target.value)
    return target


# ---------------------------------------------------------------------------
# Run states
# ---------------------------------------------------------------------------

_RUN_TRANSITIONS: Final[dict[RunState, frozenset[RunState]]] = {
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.FAILED}),
    RunState.RUNNING: frozenset(
        {
            RunState.COMPLETED,
            RunState.COMPLETED_WITH_EXCEPTIONS,
            RunState.FAILED,
        }
    ),
    # Terminal states have no outgoing edges; a re-run is a NEW run.
    RunState.COMPLETED: frozenset(),
    RunState.COMPLETED_WITH_EXCEPTIONS: frozenset(),
    RunState.FAILED: frozenset(),
}


def transition_run(current: RunState, target: RunState) -> RunState:
    if target == current:
        return current
    legal = _RUN_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise IllegalTransition("run", current.value, target.value)
    return target


def is_terminal_run(state: RunState) -> bool:
    return not _RUN_TRANSITIONS.get(state, frozenset())


# ---------------------------------------------------------------------------
# BOQ approval states
# ---------------------------------------------------------------------------

_BOQ_TRANSITIONS: Final[dict[BoqStatus, frozenset[BoqStatus]]] = {
    BoqStatus.DRAFT: frozenset({BoqStatus.IN_REVIEW}),
    BoqStatus.IN_REVIEW: frozenset({BoqStatus.REVIEWED, BoqStatus.DRAFT}),
    BoqStatus.REVIEWED: frozenset({BoqStatus.APPROVED, BoqStatus.DRAFT}),
    BoqStatus.APPROVED: frozenset({BoqStatus.EXPORTED, BoqStatus.STALE_APPROVED}),
    # Stale (inputs changed after approval) can only be re-approved from IN_REVIEW.
    BoqStatus.STALE_APPROVED: frozenset({BoqStatus.DRAFT}),
    BoqStatus.EXPORTED: frozenset(),
}


def transition_boq(current: BoqStatus, target: BoqStatus) -> BoqStatus:
    if target == current:
        return current
    legal = _BOQ_TRANSITIONS.get(current, frozenset())
    if target not in legal:
        raise IllegalTransition("boq", current.value, target.value)
    return target


def is_terminal_boq(state: BoqStatus) -> bool:
    return not _BOQ_TRANSITIONS.get(state, frozenset())


def boq_may_export(state: BoqStatus) -> bool:
    """Export gate: only APPROVED (or re-export of the same approved version)."""
    return state in (BoqStatus.APPROVED, BoqStatus.EXPORTED)
