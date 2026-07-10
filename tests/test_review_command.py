"""Unit tests for workflow review command.

Requirements tested:
  - REV-01: Approve review with exit 0
  - REV-02: Reject review with --comment
  - REV-03: Revise review with --comment
  - Validation: --comment required for reject/revise, exactly-one decision flag,
    wrong node type rejection, missing --run-id
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from cli.client import SubmitReviewResponse, WorkflowStatusResponse
from cli.commands.review import review_command
from cli.config import CLIConfig
from cli.last_run import LastRunContext, save_last_run
from cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKFLOW_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
_RUN_ID = 'run-001'
_LAST_RUN_UUID = UUID(_WORKFLOW_ID)
_NODE_HR = 'node-human-review-1'

CLI_GLOBAL_OPTS = [
    '--host',
    'https://api.example.com',
    '--api-key',
    'test-key',
    '--org',
    str(uuid4()),
]


def _make_last_run_context() -> LastRunContext:
    return LastRunContext(
        workflow_id=_LAST_RUN_UUID,
        run_id=_RUN_ID,
        instance='https://api.example.com',
        started_at=datetime(2026, 2, 26, 10, 0, 0, tzinfo=UTC),
    )


def _make_mock_config() -> MagicMock:
    return MagicMock(spec=CLIConfig)


def _make_nodes(*, hr_node_id: str = _NODE_HR) -> list[SimpleNamespace]:
    """Return mock nodes including one HUMAN_REVIEW node."""
    return [
        SimpleNamespace(id='node-agent-1', config_type=SimpleNamespace(value='AGENT')),
        SimpleNamespace(id=hr_node_id, config_type=SimpleNamespace(value='HUMAN_REVIEW')),
    ]


def _make_status_response(
    *,
    review_node_id: str | None = _NODE_HR,
) -> WorkflowStatusResponse:
    """Create a status response indicating workflow is paused for review."""
    state = {
        'execution_history': ['node-agent-1'],
        'node_outputs': {'node-agent-1': {}},
        'current_node_id': review_node_id,
        'execution_status': 'WAITING_FOR_REVIEW',
        'review_node_id': review_node_id,
    }
    return WorkflowStatusResponse(
        workflow_id=_WORKFLOW_ID,
        run_id=_RUN_ID,
        status='WAITING_FOR_REVIEW',
        current_node=review_node_id,
        state=state,
    )


def _make_review_response() -> SubmitReviewResponse:
    return SubmitReviewResponse(
        workflow_id=_WORKFLOW_ID,
        status='ok',
        message='Review submitted',
    )


def _setup_mock_client(
    mock_client_class: MagicMock,
    mock_client: MagicMock,
) -> None:
    """Wire up the mock client class's from_config context manager."""
    mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)


def _make_ready_mock_client(
    *,
    review_node_id: str = _NODE_HR,
) -> MagicMock:
    """Create a mock client configured for a paused HUMAN_REVIEW workflow."""
    mock_client = MagicMock()
    mock_client.get_workflow_status.return_value = _make_status_response(
        review_node_id=review_node_id,
    )
    mock_client.list_nodes.return_value = _make_nodes(hr_node_id=review_node_id)
    mock_client.submit_review.return_value = _make_review_response()
    return mock_client


# ---------------------------------------------------------------------------
# REV-01: Approve review
# ---------------------------------------------------------------------------


class TestReviewApprove:
    """REV-01: approve a review."""

    @patch('cli.commands.review.Confirm.ask', return_value=True)
    @patch('cli.commands.review.WorkflowClient')
    def test_review_approve(self, mock_client_class, mock_confirm, tmp_path):
        """--approve submits decision='approve' with no feedback."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            approve=True,
            working_dir=tmp_path,
        )

        mock_client.submit_review.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            decision='approve',
            feedback=None,
        )

    @patch('cli.commands.review.Confirm.ask', side_effect=AssertionError('prompt should not run'))
    @patch('cli.commands.review.WorkflowClient')
    def test_review_yes_skips_confirmation(self, mock_client_class, mock_confirm, tmp_path):
        """--yes bypasses the confirmation prompt and submits the review."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            approve=True,
            yes=True,
            working_dir=tmp_path,
        )

        mock_confirm.assert_not_called()
        mock_client.submit_review.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            decision='approve',
            feedback=None,
        )


# ---------------------------------------------------------------------------
# REV-02: Reject with comment
# ---------------------------------------------------------------------------


class TestReviewReject:
    """REV-02: reject a review with --comment."""

    @patch('cli.commands.review.Confirm.ask', return_value=True)
    @patch('cli.commands.review.WorkflowClient')
    def test_review_reject_with_comment(self, mock_client_class, mock_confirm, tmp_path):
        """--reject --comment 'Needs fixes' submits decision='reject' with feedback."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            reject=True,
            comment='Needs fixes',
            working_dir=tmp_path,
        )

        mock_client.submit_review.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            decision='reject',
            feedback='Needs fixes',
        )


# ---------------------------------------------------------------------------
# REV-03: Revise with comment
# ---------------------------------------------------------------------------


class TestReviewRevise:
    """REV-03: request revision with --comment."""

    @patch('cli.commands.review.Confirm.ask', return_value=True)
    @patch('cli.commands.review.WorkflowClient')
    def test_review_revise_with_comment(self, mock_client_class, mock_confirm, tmp_path):
        """--revise --comment 'Update section 3' submits decision='revise' with feedback."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            revise=True,
            comment='Update section 3',
            working_dir=tmp_path,
        )

        mock_client.submit_review.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            decision='revise',
            feedback='Update section 3',
        )


# ---------------------------------------------------------------------------
# Validation: --comment required for reject
# ---------------------------------------------------------------------------


class TestReviewRejectWithoutComment:
    """Validation: --reject without --comment raises ValueError."""

    def test_review_reject_without_comment(self, tmp_path):
        """--reject without --comment raises ValueError."""
        save_last_run(tmp_path, _make_last_run_context())

        with pytest.raises(ValueError, match='--comment is required'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                reject=True,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Validation: no decision flag
# ---------------------------------------------------------------------------


class TestReviewNoDecisionFlag:
    """Validation: no --approve/--reject/--revise raises ValueError."""

    def test_review_no_decision_flag(self, tmp_path):
        """No decision flag raises ValueError."""
        save_last_run(tmp_path, _make_last_run_context())

        with pytest.raises(ValueError, match='Specify exactly one'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Validation: multiple decision flags
# ---------------------------------------------------------------------------


class TestReviewMultipleDecisionFlags:
    """Validation: multiple decision flags raises ValueError."""

    def test_review_multiple_decision_flags(self, tmp_path):
        """--approve and --reject together raises ValueError."""
        save_last_run(tmp_path, _make_last_run_context())

        with pytest.raises(ValueError, match='Specify exactly one'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                approve=True,
                reject=True,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Pre-flight validation: wrong node type
# ---------------------------------------------------------------------------


class TestReviewWrongNodeType:
    """Pre-flight: node_id points to an AGENT node, not HUMAN_REVIEW."""

    @patch('cli.commands.review.WorkflowClient')
    def test_review_wrong_node_type(self, mock_client_class, tmp_path):
        """Pre-flight rejects if node is AGENT, not HUMAN_REVIEW."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response()
        # Return nodes where the requested node_id is an AGENT
        mock_client.list_nodes.return_value = [
            SimpleNamespace(id='node-agent-1', config_type=SimpleNamespace(value='AGENT')),
            SimpleNamespace(id=_NODE_HR, config_type=SimpleNamespace(value='HUMAN_REVIEW')),
        ]
        _setup_mock_client(mock_client_class, mock_client)

        # Ask for node-agent-1 which is type AGENT
        with pytest.raises(ValueError, match='not HUMAN_REVIEW'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id='node-agent-1',
                approve=True,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# REV-01: JSON output
# ---------------------------------------------------------------------------


class TestReviewJsonOutput:
    """REV-01: --json flag produces valid JSON output."""

    @patch('cli.commands.review.Confirm.ask', return_value=True)
    @patch('cli.commands.review.WorkflowClient')
    def test_review_json_output(self, mock_client_class, mock_confirm, tmp_path, capsys):
        """--approve --json outputs valid JSON response."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            approve=True,
            json_output=True,
            working_dir=tmp_path,
        )

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data['workflow_id'] == _WORKFLOW_ID
        assert data['status'] == 'ok'


# ---------------------------------------------------------------------------
# Cancelled confirmation
# ---------------------------------------------------------------------------


class TestReviewCancelled:
    """Confirmation prompt rejection cancels the operation."""

    @patch('cli.commands.review.Confirm.ask', return_value=False)
    @patch('cli.commands.review.WorkflowClient')
    def test_review_cancelled(self, mock_client_class, mock_confirm, tmp_path):
        """User declining confirmation -> no API call to submit_review."""
        save_last_run(tmp_path, _make_last_run_context())
        mock_client = _make_ready_mock_client()
        _setup_mock_client(mock_client_class, mock_client)

        review_command(
            _make_mock_config(),
            run_id=_RUN_ID,
            node_id=_NODE_HR,
            approve=True,
            working_dir=tmp_path,
        )

        mock_client.submit_review.assert_not_called()


# ---------------------------------------------------------------------------
# Missing --run-id via CLI (Typer enforces required option)
# ---------------------------------------------------------------------------


class TestReviewMissingRunId:
    """Typer enforces --run-id as a required option."""

    def test_review_missing_run_id(self):
        """Invoke review without --run-id -> non-zero exit code from Typer."""
        result = runner.invoke(
            app,
            [
                *CLI_GLOBAL_OPTS,
                'review',
                '--node-id',
                _NODE_HR,
                '--approve',
            ],
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# UX-02: Terminal state rejection
# ---------------------------------------------------------------------------


class TestReviewTerminalState:
    """UX-02: review on a completed/failed workflow raises ValueError."""

    @patch('cli.commands.review.WorkflowClient')
    def test_review_terminal_completed(self, mock_client_class, tmp_path):
        """Review on a completed workflow raises ValueError with 'has completed'."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = WorkflowStatusResponse(
            workflow_id=_WORKFLOW_ID,
            run_id=_RUN_ID,
            status='COMPLETED',
            current_node=None,
            state={
                'execution_history': ['node-agent-1', _NODE_HR],
                'node_outputs': {'node-agent-1': {}, _NODE_HR: {}},
                'current_node_id': None,
                'execution_status': 'COMPLETED',
            },
        )
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        with pytest.raises(ValueError, match='has completed'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                approve=True,
                working_dir=tmp_path,
            )

        mock_client.submit_review.assert_not_called()

    @patch('cli.commands.review.WorkflowClient')
    def test_review_terminal_failed(self, mock_client_class, tmp_path):
        """Review on a failed workflow raises ValueError with 'has failed'."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = WorkflowStatusResponse(
            workflow_id=_WORKFLOW_ID,
            run_id=_RUN_ID,
            status='FAILED',
            current_node=None,
            state={
                'execution_history': ['node-agent-1'],
                'node_outputs': {},
                'current_node_id': None,
                'execution_status': 'FAILED',
            },
        )
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        with pytest.raises(ValueError, match='has failed'):
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                approve=True,
                working_dir=tmp_path,
            )

        mock_client.submit_review.assert_not_called()


# ---------------------------------------------------------------------------
# UX-02: Wrong HITL type (input instead of review)
# ---------------------------------------------------------------------------


class TestReviewWrongHitlType:
    """UX-02: review on an input-paused workflow raises ValueError."""

    @patch('cli.commands.review.WorkflowClient')
    def test_review_wrong_hitl_input(self, mock_client_class, tmp_path):
        """Review when workflow is paused for input raises ValueError suggesting 'workflow input'."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = WorkflowStatusResponse(
            workflow_id=_WORKFLOW_ID,
            run_id=_RUN_ID,
            status='WAITING_FOR_INPUT',
            current_node='node-input-1',
            state={
                'execution_history': ['node-agent-1'],
                'node_outputs': {'node-agent-1': {}},
                'current_node_id': 'node-input-1',
                'execution_status': 'WAITING_FOR_INPUT',
                'waiting_input_node_id': 'node-input-1',
            },
        )
        mock_client.list_nodes.return_value = [
            SimpleNamespace(id='node-agent-1', config_type=SimpleNamespace(value='AGENT')),
            SimpleNamespace(
                id='node-input-1', config_type=SimpleNamespace(value='PLAIN_TXT_INPUT')
            ),
            SimpleNamespace(id=_NODE_HR, config_type=SimpleNamespace(value='HUMAN_REVIEW')),
        ]
        _setup_mock_client(mock_client_class, mock_client)

        with pytest.raises(ValueError, match='paused for input') as exc_info:
            review_command(
                _make_mock_config(),
                run_id=_RUN_ID,
                node_id=_NODE_HR,
                approve=True,
                working_dir=tmp_path,
            )

        assert 'node-input-1' in str(exc_info.value)
        assert 'workflow input' in str(exc_info.value)
        mock_client.submit_review.assert_not_called()
