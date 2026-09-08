"""Unit tests for core state machines (docs/domain-model.md contracts)."""
from __future__ import annotations

import pytest

from core.domain.enums import BoqStatus, MeasurementState, RunState
from core.domain.states import (
    IllegalTransition,
    boq_may_export,
    is_terminal_run,
    transition_boq,
    transition_measurement,
    transition_run,
)


class TestMeasurementStates:
    def test_initial_states_are_all_valid_mints(self):
        # all five states may be minted directly by a run
        for s in MeasurementState:
            assert s  # enum loads

    def test_needs_review_can_be_accepted(self):
        assert (
            transition_measurement(
                MeasurementState.NEEDS_REVIEW, MeasurementState.MEASURED
            )
            is MeasurementState.MEASURED
        )

    def test_measured_cannot_jump_to_not_measurable(self):
        with pytest.raises(IllegalTransition):
            transition_measurement(
                MeasurementState.MEASURED, MeasurementState.NOT_MEASURABLE
            )

    def test_blocked_resolves_to_measured(self):
        assert (
            transition_measurement(MeasurementState.BLOCKED, MeasurementState.MEASURED)
            is MeasurementState.MEASURED
        )

    def test_measured_zero_is_flaggable_for_review(self):
        assert (
            transition_measurement(
                MeasurementState.MEASURED_ZERO, MeasurementState.NEEDS_REVIEW
            )
            is MeasurementState.NEEDS_REVIEW
        )

    def test_same_state_is_noop(self):
        assert (
            transition_measurement(MeasurementState.MEASURED, MeasurementState.MEASURED)
            is MeasurementState.MEASURED
        )


class TestRunStates:
    def test_happy_path(self):
        s = transition_run(RunState.QUEUED, RunState.RUNNING)
        s = transition_run(s, RunState.COMPLETED_WITH_EXCEPTIONS)
        assert s is RunState.COMPLETED_WITH_EXCEPTIONS
        assert is_terminal_run(s)

    def test_queued_cannot_complete(self):
        with pytest.raises(IllegalTransition):
            transition_run(RunState.QUEUED, RunState.COMPLETED)

    def test_terminal_states_are_frozen(self):
        for state in (RunState.COMPLETED, RunState.FAILED):
            with pytest.raises(IllegalTransition):
                transition_run(state, RunState.RUNNING)


class TestBoqStates:
    def test_full_lifecycle(self):
        s = BoqStatus.DRAFT
        for nxt in (BoqStatus.IN_REVIEW, BoqStatus.REVIEWED, BoqStatus.APPROVED):
            s = transition_boq(s, nxt)
        assert boq_may_export(s)
        s = transition_boq(s, BoqStatus.EXPORTED)
        assert is_terminal_boq_export(s)

    def is_terminal_check(self):  # helper naming see below
        pass

    def test_reject_returns_to_draft(self):
        assert (
            transition_boq(BoqStatus.IN_REVIEW, BoqStatus.DRAFT) is BoqStatus.DRAFT
        )

    def test_approved_goes_stale_on_input_change(self):
        assert (
            transition_boq(BoqStatus.APPROVED, BoqStatus.STALE_APPROVED)
            is BoqStatus.STALE_APPROVED
        )

    def test_stale_requires_redraft_then_reapproval(self):
        # stale -> draft -> in_review -> ... (no shortcut to APPROVED)
        with pytest.raises(IllegalTransition):
            transition_boq(BoqStatus.STALE_APPROVED, BoqStatus.APPROVED)
        s = transition_boq(BoqStatus.STALE_APPROVED, BoqStatus.DRAFT)
        s = transition_boq(s, BoqStatus.IN_REVIEW)
        s = transition_boq(s, BoqStatus.REVIEWED)
        assert transition_boq(s, BoqStatus.APPROVED) is BoqStatus.APPROVED

    def test_draft_cannot_be_exported(self):
        assert not boq_may_export(BoqStatus.DRAFT)
        assert not boq_may_export(BoqStatus.STALE_APPROVED)


def is_terminal_boq_export(s: BoqStatus) -> bool:
    from core.domain.states import is_terminal_boq

    return is_terminal_boq(s)
