"""Unit tests for workflow input command.

Requirements tested:
  - INPUT-01: Submit input data (JSON string, @filepath), confirmation, --json output
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from cli.client import SubmitInputResponse
from cli.last_run import LastRunContext, save_last_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKFLOW_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
_RUN_ID = 'run-001'
_LAST_RUN_UUID = UUID(_WORKFLOW_ID)
_NODE_ID = 'node-input-1'


def _make_last_run_context() -> LastRunContext:
    return LastRunContext(
        workflow_id=_LAST_RUN_UUID,
        run_id=_RUN_ID,
        instance='https://api.example.com',
        started_at=datetime(2026, 2, 26, 10, 0, 0, tzinfo=UTC),
    )


def _make_submit_response() -> SubmitInputResponse:
    return SubmitInputResponse(
        workflow_id=_WORKFLOW_ID,
        node_id=_NODE_ID,
        status='ok',
        message='Input received',
    )


def _make_mock_config() -> MagicMock:
    from cli.config import CLIConfig

    return MagicMock(spec=CLIConfig)


# ---------------------------------------------------------------------------
# INPUT-01: Submit JSON string data
# ---------------------------------------------------------------------------


class TestInputSubmitJsonData:
    """INPUT-01: submit inline JSON data via --data flag."""

    @patch('cli.commands.input.Confirm.ask', return_value=True)
    @patch('cli.commands.input.WorkflowClient')
    def test_input_submit_json_data(self, mock_client_class, mock_confirm, tmp_path):
        """Submit JSON string data with confirmation -> success message."""
        from cli.commands.input import input_command

        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.submit_input.return_value = _make_submit_response()
        mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)

        config = _make_mock_config()
        input_command(
            config,
            node_id=_NODE_ID,
            data='{"text": "hello"}',
            working_dir=tmp_path,
        )

        mock_client.submit_input.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            node_id=_NODE_ID,
            input_data={'text': 'hello'},
        )
        mock_confirm.assert_called_once()


# ---------------------------------------------------------------------------
# INPUT-01: Submit file data
# ---------------------------------------------------------------------------


class TestInputSubmitFileData:
    """INPUT-01: submit data via @filepath."""

    @patch('cli.commands.input.Confirm.ask', return_value=True)
    @patch('cli.commands.input.WorkflowClient')
    def test_input_submit_file_data(self, mock_client_class, mock_confirm, tmp_path):
        """Submit @filepath data -> file content is parsed and sent."""
        from cli.commands.input import input_command

        save_last_run(tmp_path, _make_last_run_context())

        # Create a JSON file to read from
        input_file = tmp_path / 'input.json'
        input_file.write_text(json.dumps({'question': 'What is AI?'}))

        mock_client = MagicMock()
        mock_client.submit_input.return_value = _make_submit_response()
        mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)

        config = _make_mock_config()
        input_command(
            config,
            node_id=_NODE_ID,
            data=f'@{input_file}',
            working_dir=tmp_path,
        )

        mock_client.submit_input.assert_called_once_with(
            _WORKFLOW_ID,
            run_id=_RUN_ID,
            node_id=_NODE_ID,
            input_data={'question': 'What is AI?'},
        )


# ---------------------------------------------------------------------------
# Cancelled confirmation
# ---------------------------------------------------------------------------


class TestInputCancelled:
    """Confirmation prompt rejection cancels the operation."""

    @patch('cli.commands.input.Confirm.ask', return_value=False)
    @patch('cli.commands.input.WorkflowClient')
    def test_input_cancelled(self, mock_client_class, mock_confirm, tmp_path):
        """User declining confirmation -> Cancelled message, no API call."""
        from cli.commands.input import input_command

        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)

        config = _make_mock_config()
        input_command(
            config,
            node_id=_NODE_ID,
            data='{"text": "hello"}',
            working_dir=tmp_path,
        )

        # submit_input should NOT be called
        mock_client.submit_input.assert_not_called()


# ---------------------------------------------------------------------------
# INPUT-01: JSON output
# ---------------------------------------------------------------------------


class TestInputJsonOutput:
    """INPUT-01: --json flag produces valid JSON output."""

    @patch('cli.commands.input.Confirm.ask', return_value=True)
    @patch('cli.commands.input.WorkflowClient')
    def test_input_json_output(self, mock_client_class, mock_confirm, tmp_path, capsys):
        """--json flag outputs valid JSON with response data."""
        from cli.commands.input import input_command

        save_last_run(tmp_path, _make_last_run_context())

        mock_client = MagicMock()
        mock_client.submit_input.return_value = _make_submit_response()
        mock_client_class.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.from_config.return_value.__exit__ = MagicMock(return_value=False)

        config = _make_mock_config()
        input_command(
            config,
            node_id=_NODE_ID,
            data='{"text": "hello"}',
            json_output=True,
            working_dir=tmp_path,
        )

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data['workflow_id'] == _WORKFLOW_ID
        assert data['node_id'] == _NODE_ID
        assert data['status'] == 'ok'


# ---------------------------------------------------------------------------
# Error handling: missing .last_run
# ---------------------------------------------------------------------------


class TestInputNoLastRun:
    """Error when .last_run missing and no --run-id provided."""

    def test_input_no_last_run(self, tmp_path):
        """No .last_run and no --run-id raises ValueError."""
        from cli.commands.input import input_command

        config = _make_mock_config()
        with pytest.raises(ValueError, match=r'(?i)\.last_run|No .last_run'):
            input_command(
                config,
                node_id=_NODE_ID,
                data='{"text": "hello"}',
                working_dir=tmp_path,
            )
