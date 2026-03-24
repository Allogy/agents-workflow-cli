"""Tests that CLI shared-model enums cover all backend execution states."""

from workflow_models.enums import ExecutionStatus, NodeExecutionStatus


class TestExecutionStatusCompleteness:
    """ExecutionStatus must cover all states the backend can send."""

    def test_has_paused(self):
        assert hasattr(ExecutionStatus, 'PAUSED')
        assert ExecutionStatus.PAUSED.value == 'PAUSED'

    def test_has_timed_out(self):
        """Backend durable execution sends TIMED_OUT, not TIMEOUT."""
        assert hasattr(ExecutionStatus, 'TIMED_OUT')
        assert ExecutionStatus.TIMED_OUT.value == 'TIMED_OUT'

    def test_has_waiting_for_review(self):
        assert hasattr(ExecutionStatus, 'WAITING_FOR_REVIEW')
        assert ExecutionStatus.WAITING_FOR_REVIEW.value == 'WAITING_FOR_REVIEW'

    def test_has_waiting_for_input(self):
        assert hasattr(ExecutionStatus, 'WAITING_FOR_INPUT')
        assert ExecutionStatus.WAITING_FOR_INPUT.value == 'WAITING_FOR_INPUT'

    def test_timeout_removed_from_enum(self):
        """TIMEOUT must not be an enum member — TIMED_OUT is the canonical value.

        The old TIMEOUT value created confusion because it was a distinct enum
        member, not an alias. Defensive string parsing happens in run.py sets,
        not in the enum itself.
        """
        assert not hasattr(ExecutionStatus, 'TIMEOUT')
        assert hasattr(ExecutionStatus, 'TIMED_OUT')


class TestNodeExecutionStatusCompleteness:
    """NodeExecutionStatus must cover all per-node states."""

    def test_has_waiting_input(self):
        assert hasattr(NodeExecutionStatus, 'WAITING_INPUT')
        assert NodeExecutionStatus.WAITING_INPUT.value == 'WAITING_INPUT'
