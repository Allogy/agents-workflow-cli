"""Unit tests for workflow status command.

Requirements tested:
  - STAT-01: Status check with run ID, node-by-node output, --json mode, paused-node hints
  - STAT-02: .last_run fallback, error on missing context
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from cli.client import WorkflowStatusResponse
from cli.commands.status import status_command
from cli.config import CLIConfig
from cli.last_run import LastRunContext, save_last_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKFLOW_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
_RUN_ID = 'run-001'
_LAST_RUN_UUID = UUID(_WORKFLOW_ID)


def _make_last_run_context() -> LastRunContext:
    return LastRunContext(
        workflow_id=_LAST_RUN_UUID,
        run_id=_RUN_ID,
        instance='https://api.example.com',
        started_at=datetime(2026, 2, 26, 10, 0, 0, tzinfo=UTC),
    )


def _make_mock_config() -> MagicMock:
    return MagicMock(spec=CLIConfig)


def _make_nodes() -> list[SimpleNamespace]:
    """Return 3 mock nodes with different config_types."""
    return [
        SimpleNamespace(id='node-agent-1', config_type=SimpleNamespace(value='AGENT')),
        SimpleNamespace(id='node-input-2', config_type=SimpleNamespace(value='PLAIN_TXT_INPUT')),
        SimpleNamespace(id='node-review-3', config_type=SimpleNamespace(value='HUMAN_REVIEW')),
    ]


def _make_status_response(
    *,
    status: str = 'RUNNING',
    execution_history: list[str] | None = None,
    node_outputs: dict | None = None,
    current_node_id: str | None = None,
    execution_status: str | None = None,
    waiting_input_node_id: str | None = None,
    review_node_id: str | None = None,
) -> WorkflowStatusResponse:
    state = {
        'execution_history': execution_history or [],
        'node_outputs': node_outputs or {},
        'current_node_id': current_node_id,
        'execution_status': execution_status or status,
    }
    if waiting_input_node_id:
        state['waiting_input_node_id'] = waiting_input_node_id
    if review_node_id:
        state['review_node_id'] = review_node_id
    return WorkflowStatusResponse(
        workflow_id=_WORKFLOW_ID,
        run_id=_RUN_ID,
        status=status,
        current_node=current_node_id,
        state=state,
    )


def _setup_mock_client(
    mock_client_class: MagicMock,
    mock_client: MagicMock,
) -> None:
    """Wire up the mock client class's from_config context manager."""
    mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)


# ---------------------------------------------------------------------------
# STAT-01 + STAT-02: Status with .last_run fallback
# ---------------------------------------------------------------------------


class TestStatusWithLastRun:
    """STAT-01 + STAT-02: status via .last_run context, node-by-node output."""

    @patch('cli.commands.status.WorkflowClient')
    def test_status_with_last_run_direct(self, mock_client_class, tmp_path):
        """Status reads .last_run, calls API with correct workflow_id and run_id."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response(
            execution_history=['node-agent-1'],
            node_outputs={'node-agent-1': {'result': 'ok'}},
            current_node_id='node-input-2',
            execution_status='RUNNING',
        )
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        status_command(_make_mock_config(), run_id=None, json_output=False, working_dir=tmp_path)

        mock_client.get_workflow_status.assert_called_once_with(_WORKFLOW_ID, _RUN_ID)
        mock_client.list_nodes.assert_called_once_with(_WORKFLOW_ID)


# ---------------------------------------------------------------------------
# STAT-01: Explicit run-id override
# ---------------------------------------------------------------------------


class TestStatusExplicitRunId:
    """STAT-01: explicit run_id overrides .last_run's run_id."""

    @patch('cli.commands.status.WorkflowClient')
    def test_status_with_explicit_run_id(self, mock_client_class, tmp_path):
        """Explicit run-id argument overrides .last_run run_id."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response()
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        explicit_run_id = 'explicit-run-id-123'
        status_command(
            _make_mock_config(),
            run_id=explicit_run_id,
            json_output=False,
            working_dir=tmp_path,
        )

        # Should use the explicit run_id, NOT the .last_run run_id
        mock_client.get_workflow_status.assert_called_once_with(_WORKFLOW_ID, explicit_run_id)


# ---------------------------------------------------------------------------
# STAT-01: JSON output
# ---------------------------------------------------------------------------


class TestStatusJsonOutput:
    """STAT-01: --json flag produces valid JSON output."""

    @patch('cli.commands.status.WorkflowClient')
    def test_status_json_output(self, mock_client_class, tmp_path, capsys):
        """--json flag outputs valid JSON with workflow_id, run_id, status."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response()
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        status_command(_make_mock_config(), run_id=None, json_output=True, working_dir=tmp_path)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data['workflow_id'] == _WORKFLOW_ID
        assert data['run_id'] == _RUN_ID
        assert data['status'] == 'RUNNING'


# ---------------------------------------------------------------------------
# STAT-02: Missing .last_run with no explicit run-id
# ---------------------------------------------------------------------------


class TestStatusNoLastRun:
    """STAT-02: error when .last_run missing and no run-id provided."""

    def test_status_no_last_run_no_run_id(self, tmp_path):
        """No .last_run and no run_id raises ValueError."""
        with pytest.raises(ValueError, match=r'(?i)\.last_run|No .last_run'):
            status_command(
                _make_mock_config(),
                run_id=None,
                json_output=False,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# STAT-01: Paused node hints
# ---------------------------------------------------------------------------


class TestStatusPausedNodeHints:
    """STAT-01: actionable hints for paused nodes."""

    @patch('cli.commands.status.WorkflowClient')
    def test_status_paused_input_hints(self, mock_client_class, tmp_path, capsys):
        """Paused WAITING_FOR_INPUT node shows Tip with node ID."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response(
            waiting_input_node_id='node-input-2',
        )
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        status_command(_make_mock_config(), run_id=None, json_output=False, working_dir=tmp_path)

        captured = capsys.readouterr()
        assert 'Tip:' in captured.out
        assert 'node-input-2' in captured.out

    @patch('cli.commands.status.WorkflowClient')
    def test_status_paused_review_hints(self, mock_client_class, tmp_path, capsys):
        """Paused WAITING_FOR_REVIEW node shows Tip with node ID and --approve hint."""
        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = _make_status_response(
            review_node_id='node-review-3',
        )
        mock_client.list_nodes.return_value = _make_nodes()
        _setup_mock_client(mock_client_class, mock_client)

        status_command(_make_mock_config(), run_id=None, json_output=False, working_dir=tmp_path)

        captured = capsys.readouterr()
        assert 'Tip:' in captured.out
        assert 'node-review-3' in captured.out
        assert '--approve' in captured.out
