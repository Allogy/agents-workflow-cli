"""Unit tests for workflow run command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest
from typer.testing import CliRunner

from cli.commands.run import (
    _print_final_status,
    format_sse_event,
    parse_input_arg,
    resolve_workflow_id,
    run_command,
    run_polling,
    run_streaming,
)
from cli.last_run import load_last_run
from cli.main import app
from cli.sse import SSEEvent

# ---------------------------------------------------------------------------
# Input parsing tests
# ---------------------------------------------------------------------------


class TestParseInputArg:
    def test_none_returns_empty_dict(self) -> None:
        """No --input flag returns empty dict."""
        assert parse_input_arg(None) == {}

    def test_json_string(self) -> None:
        """Inline JSON string is parsed."""
        result = parse_input_arg('{"question": "What is AI?"}')
        assert result == {'question': 'What is AI?'}

    def test_file_reference(self, tmp_path: Path) -> None:
        """@filepath reads and parses the file."""
        f = tmp_path / 'input.json'
        f.write_text(json.dumps({'key': 'value'}))
        result = parse_input_arg(f'@{f}')
        assert result == {'key': 'value'}

    def test_file_not_found_raises(self) -> None:
        """@nonexistent raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_input_arg('@/nonexistent/file.json')

    def test_invalid_json_raises(self) -> None:
        """Invalid JSON string raises ValueError."""
        with pytest.raises(ValueError, match='Invalid JSON'):
            parse_input_arg('not json')

    def test_non_dict_json_raises(self) -> None:
        """JSON array raises ValueError (only objects accepted)."""
        with pytest.raises(ValueError, match='expected an object'):
            parse_input_arg('[1, 2, 3]')


# ---------------------------------------------------------------------------
# Identifier resolution tests
# ---------------------------------------------------------------------------


class TestResolveWorkflowId:
    def test_uuid_passthrough(self) -> None:
        """A UUID string is returned as-is without API calls."""
        mock_client = MagicMock()
        result = resolve_workflow_id('939843a8-6257-4475-bfc0-f7d6500d9f00', mock_client, None)
        assert result == '939843a8-6257-4475-bfc0-f7d6500d9f00'
        mock_client.list_workflows.assert_not_called()

    def test_lockfile_lookup(self, tmp_path: Path) -> None:
        """If a .workflow.lock in cwd has a matching name, use its UUID."""
        from datetime import UTC, datetime

        from cli.lockfile import WorkflowLock, write_lockfile

        lock = WorkflowLock(
            workflow_id=UUID('11111111-2222-3333-4444-555555555555'),
            organization_id=UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
            version=1,
            instance='https://api.example.com',
            pushed_at=datetime.now(UTC),
        )
        lock_path = tmp_path / 'invoices.workflow.lock'
        write_lockfile(lock_path, lock)

        # We also need the YAML file to exist so we can read its name
        yaml_path = tmp_path / 'invoices.workflow.yaml'
        yaml_path.write_text('name: Invoice Processing\nversion: 1\nnodes: {}\nedges: []\n')

        mock_client = MagicMock()
        result = resolve_workflow_id('Invoice Processing', mock_client, None, search_dir=tmp_path)
        assert result == '11111111-2222-3333-4444-555555555555'
        mock_client.list_workflows.assert_not_called()

    def test_api_name_lookup(self) -> None:
        """Falls back to API when no lockfile match."""
        mock_client = MagicMock()
        mock_metadata = MagicMock()
        mock_metadata.name = 'Invoice Processing'
        mock_client.list_workflows.return_value = [
            MagicMock(id=UUID('11111111-2222-3333-4444-555555555555')),
        ]
        mock_client.get_metadata.return_value = mock_metadata

        result = resolve_workflow_id('Invoice Processing', mock_client, 'org-id')
        assert result == '11111111-2222-3333-4444-555555555555'

    def test_not_found_raises(self) -> None:
        """Unknown name raises ValueError."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        with pytest.raises(ValueError, match='not found'):
            resolve_workflow_id('Nonexistent', mock_client, 'org-id')


# ---------------------------------------------------------------------------
# Polling execution tests
# ---------------------------------------------------------------------------


class TestRunPolling:
    def test_completed_workflow(self) -> None:
        """Polling exits cleanly when workflow completes."""
        mock_client = MagicMock()

        # First poll: RUNNING, second poll: COMPLETED
        mock_client.get_workflow_status.side_effect = [
            MagicMock(status='RUNNING', current_node='node1', state={}),
            MagicMock(status='COMPLETED', current_node=None, state={}),
        ]

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'COMPLETED'

    def test_step_counter_with_total_nodes(self) -> None:
        """Polling displays step counter when total_nodes is provided."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.side_effect = [
            MagicMock(status='RUNNING', current_node='node1', state={}),
            MagicMock(status='RUNNING', current_node='node2', state={}),
            MagicMock(status='COMPLETED', current_node=None, state={}),
        ]

        result = run_polling(mock_client, 'wf-id', 'run-id', total_nodes=3, poll_interval=0)
        assert result == 'COMPLETED'
        assert mock_client.get_workflow_status.call_count == 3

    def test_failed_workflow(self) -> None:
        """Polling returns FAILED status."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='FAILED', current_node=None, state={}
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'FAILED'

    def test_waiting_for_review_exits(self) -> None:
        """Polling exits on WAITING_FOR_REVIEW with the gate status."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_REVIEW', current_node='review_node', state={}
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'WAITING_FOR_REVIEW'

    def test_waiting_for_input_exits(self) -> None:
        """Polling exits on WAITING_FOR_INPUT with the gate status."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT', current_node='input_node', state={}
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'WAITING_FOR_INPUT'


# ---------------------------------------------------------------------------
# SSE streaming tests
# ---------------------------------------------------------------------------


class TestFormatSseEvent:
    def test_run_started(self) -> None:
        event = SSEEvent(event_type='RUN_STARTED', data={'type': 'RUN_STARTED'})
        line = format_sse_event(event)
        assert 'RUN_STARTED' in line

    def test_step_started(self) -> None:
        event = SSEEvent(
            event_type='STEP_STARTED',
            data={'type': 'STEP_STARTED', 'node_id': 'extract', 'step_type': 'LLM_CALL'},
        )
        line = format_sse_event(event)
        assert 'extract' in line

    def test_step_finished(self) -> None:
        event = SSEEvent(
            event_type='STEP_FINISHED',
            data={'type': 'STEP_FINISHED', 'node_id': 'extract'},
        )
        line = format_sse_event(event)
        assert 'extract' in line

    def test_waiting_for_review(self) -> None:
        event = SSEEvent(
            event_type='WAITING_FOR_REVIEW',
            data={'type': 'WAITING_FOR_REVIEW', 'node_id': 'review1'},
        )
        line = format_sse_event(event)
        assert 'WAITING_FOR_REVIEW' in line

    def test_run_error(self) -> None:
        event = SSEEvent(
            event_type='RUN_ERROR',
            data={'type': 'RUN_ERROR', 'error': 'Something broke'},
        )
        line = format_sse_event(event)
        assert 'RUN_ERROR' in line


class TestRunStreaming:
    def test_completed_stream(self) -> None:
        """Streaming returns final status from events."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "STEP_STARTED", "node_id": "n1"}',
            'data: {"type": "STEP_FINISHED", "node_id": "n1"}',
            'data: {"type": "RUN_FINISHED"}',
        ]
        result = run_streaming(iter(lines))
        assert result == 'RUN_FINISHED'

    def test_hitl_gate_stops_stream(self) -> None:
        """Streaming returns on WAITING_FOR_REVIEW."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "WAITING_FOR_REVIEW", "node_id": "r1"}',
        ]
        result = run_streaming(iter(lines))
        assert result == 'WAITING_FOR_REVIEW'


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


class TestPrintFinalStatus:
    def test_cancelled_status(self) -> None:
        """CANCELLED status prints appropriate message."""
        _print_final_status('CANCELLED', 'run-id')

    def test_timed_out_status(self) -> None:
        """TIMED_OUT status prints appropriate message."""
        _print_final_status('TIMED_OUT', 'run-id')

    def test_waiting_for_review_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """WAITING_FOR_REVIEW prints a next-step hint with the review command."""
        _print_final_status('WAITING_FOR_REVIEW', 'test-run-id')
        captured = capsys.readouterr()
        assert 'review' in captured.out.lower() or 'review' in str(captured)

    def test_waiting_for_input_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """WAITING_FOR_INPUT prints a next-step hint with the input command."""
        _print_final_status('WAITING_FOR_INPUT', 'test-run-id')
        captured = capsys.readouterr()
        assert 'input' in captured.out.lower() or 'input' in str(captured)


def _make_mock_client(**overrides: Any) -> MagicMock:
    """Create a mock WorkflowClient with sensible defaults."""
    mock = MagicMock()
    mock.list_nodes.return_value = overrides.get('nodes', [MagicMock(), MagicMock(), MagicMock()])
    mock.start_workflow_temporal.return_value = overrides.get(
        'start_resp',
        MagicMock(run_id='test-run-id', workflow_id='wf-123', status='RUNNING'),
    )
    return mock


def _make_mock_config() -> MagicMock:
    """Create a mock CLIConfig."""
    mock = MagicMock()
    mock.host = 'https://api.example.com'
    mock.org_id = 'org-123'
    return mock


class TestRunCommandExitCode:
    def test_failed_workflow_exits_with_code_1(self, tmp_path: Path) -> None:
        """Polling mode exits with code 1 when workflow fails."""
        mock_client = _make_mock_client()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='FAILED',
            current_node=None,
            state={},
        )

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(SystemExit) as exc_info:
                run_command(
                    config=_make_mock_config(),
                    identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                    input_data=None,
                    stream=False,
                    no_follow=False,
                    working_dir=tmp_path,
                )
            assert exc_info.value.code == 1


class TestRunCommand:
    def test_no_follow_mode_writes_last_run(self, tmp_path: Path) -> None:
        """--no-follow starts workflow, writes .last_run, returns immediately."""
        mock_client = _make_mock_client()

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=False,
                no_follow=True,
                working_dir=tmp_path,
            )

        # .last_run should have been written
        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'test-run-id'

    def test_polling_completed_workflow(self, tmp_path: Path) -> None:
        """Polling mode exits cleanly when workflow completes."""
        mock_client = _make_mock_client()
        mock_client.get_workflow_status.side_effect = [
            MagicMock(status='RUNNING', current_node='node1', state={}),
            MagicMock(status='COMPLETED', current_node=None, state={}),
        ]

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            # Should not raise
            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data='{"question": "test"}',
                stream=False,
                no_follow=False,
                working_dir=tmp_path,
            )

        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'test-run-id'

    def test_streaming_mode_writes_last_run(self, tmp_path: Path) -> None:
        """SSE streaming mode writes .last_run and processes events."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                'data: {"type": "RUN_STARTED"}',
                'data: {"type": "STEP_STARTED", "node_id": "n1"}',
                'data: {"type": "STEP_FINISHED", "node_id": "n1"}',
                'data: {"type": "RUN_FINISHED"}',
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            # Should not raise
            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                no_follow=False,
                working_dir=tmp_path,
            )

        ctx = load_last_run(tmp_path)
        assert ctx is not None
        mock_client.stream_workflow_temporal.assert_called_once()


# ---------------------------------------------------------------------------
# Case-insensitive status tests (AUDIT-02)
# ---------------------------------------------------------------------------


class TestPollingCaseInsensitive:
    def test_polling_exits_on_lowercase_completed(self) -> None:
        """Polling terminates when backend returns lowercase 'completed'."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='completed', current_node=None, state={}
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'completed'

    def test_polling_exits_on_lowercase_waiting_for_review(self) -> None:
        """Polling terminates when backend returns lowercase 'waiting_for_review'."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='waiting_for_review', current_node='review_node', state={}
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'waiting_for_review'


class TestStreamingCaseInsensitive:
    def test_streaming_exits_on_lowercase_run_finished(self) -> None:
        """Streaming terminates when event type is lowercase 'run_finished'."""
        lines = [
            'data: {"type": "run_finished"}',
        ]
        result = run_streaming(iter(lines))
        assert result == 'run_finished'


# ---------------------------------------------------------------------------
# Timeout tests (AUDIT-03)
# ---------------------------------------------------------------------------


class TestPollingTimeout:
    def test_polling_raises_on_timeout(self) -> None:
        """Polling exits with code 1 after max_timeout_seconds elapses."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='RUNNING', current_node='node1', state={}
        )

        with pytest.raises(SystemExit) as exc_info:
            run_polling(
                mock_client,
                'wf-id',
                'run-id',
                poll_interval=0.01,
                max_timeout_seconds=0.05,
            )
        assert exc_info.value.code == 1


class TestStreamingTimeout:
    def test_streaming_raises_on_timeout(self) -> None:
        """Streaming exits with code 1 after max_timeout_seconds elapses."""
        import itertools

        # Infinite iterator of non-terminal events
        infinite_lines = itertools.cycle(
            [
                'data: {"type": "STEP_STARTED", "node_id": "n1"}',
            ]
        )

        with pytest.raises(SystemExit) as exc_info:
            run_streaming(infinite_lines, max_timeout_seconds=0.05)
        assert exc_info.value.code == 1


class TestTimeoutEnvVar:
    def test_timeout_env_var_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WORKFLOW_RUN_TIMEOUT env var is read as timeout in seconds."""
        from cli.config import get_run_timeout

        monkeypatch.setenv('WORKFLOW_RUN_TIMEOUT', '60')
        assert get_run_timeout() == 60

    def test_timeout_cli_flag_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI flag takes precedence over env var."""
        from cli.config import get_run_timeout

        monkeypatch.setenv('WORKFLOW_RUN_TIMEOUT', '60')
        assert get_run_timeout(cli_flag=120) == 120

    def test_timeout_default_is_1800(self) -> None:
        """Without env var or flag, default is 1800 seconds (30 min)."""
        from cli.config import get_run_timeout

        assert get_run_timeout() == 1800


# ---------------------------------------------------------------------------
# Name resolution with suggestions tests (RUN-02)
# ---------------------------------------------------------------------------


class TestNameResolutionSuggestions:
    def _make_client_with_names(self, names: list[str]) -> MagicMock:
        """Create a mock client returning workflows with the given names."""
        mock_client = MagicMock()
        workflows = []
        for i, _name in enumerate(names):
            wf = MagicMock()
            wf.id = UUID(f'1111111{i}-2222-3333-4444-555555555555')
            workflows.append(wf)
        mock_client.list_workflows.return_value = workflows

        def _get_metadata(wf_id: Any) -> MagicMock:
            for j, wf in enumerate(workflows):
                if wf.id == wf_id:
                    meta = MagicMock()
                    meta.name = names[j]
                    return meta
            raise Exception('Not found')

        mock_client.get_metadata.side_effect = _get_metadata
        return mock_client

    def test_name_resolution_suggests_close_matches(self) -> None:
        """Misspelled name gets 'did you mean?' suggestions with original casing."""
        client = self._make_client_with_names(['Invoice Processing', 'Invoice QA', 'Order Flow'])
        with pytest.raises(ValueError, match='Did you mean') as exc_info:
            resolve_workflow_id('Invoce', client, 'org-id')
        # At least one suggestion should appear with original casing
        msg = str(exc_info.value)
        assert 'Invoice Processing' in msg or 'Invoice QA' in msg

    def test_name_resolution_no_suggestions_when_no_close_match(self) -> None:
        """Completely unrelated name gets no 'did you mean?' suggestions."""
        client = self._make_client_with_names(['Invoice Processing', 'Invoice QA', 'Order Flow'])
        with pytest.raises(ValueError, match='No workflow found matching') as exc_info:
            resolve_workflow_id('zzz-no-match-zzz', client, 'org-id')
        msg = str(exc_info.value)
        assert 'Did you mean' not in msg

    def test_name_resolution_case_insensitive(self) -> None:
        """Exact match (case-insensitive) returns UUID without suggestions."""
        client = self._make_client_with_names(['Invoice Processing'])
        result = resolve_workflow_id('invoice processing', client, 'org-id')
        assert result == '11111110-2222-3333-4444-555555555555'


# ---------------------------------------------------------------------------
# Exit code differentiation tests (RUN-01)
# ---------------------------------------------------------------------------

runner = CliRunner()


class TestRunCommandExitCodes:
    def test_invalid_json_exits_with_code_2(self) -> None:
        """Invalid JSON --input exits with code 2 (user error, not 1)."""
        result = runner.invoke(
            app,
            ['run', '939843a8-6257-4475-bfc0-f7d6500d9f00', '--input', 'not-json'],
        )
        assert result.exit_code == 2

    def test_workflow_not_found_exits_with_code_2(self) -> None:
        """Workflow not found exits with code 2 (user error, not 1)."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client.list_nodes.return_value = []

        with (
            patch('cli.commands.run.WorkflowClient') as MockClient,
            patch('cli.main.load_config') as mock_load_config,
        ):
            mock_cfg = _make_mock_config()
            mock_load_config.return_value = mock_cfg
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(app, ['run', 'Nonexistent-Workflow'])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Network retry tests (RUN-01)
# ---------------------------------------------------------------------------


class TestRunPollingRetry:
    def test_network_error_retries_then_succeeds(self) -> None:
        """Network error on first poll retries and succeeds on second."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.side_effect = [
            httpx.ConnectError('Connection refused'),
            MagicMock(status='COMPLETED', current_node=None, state={}),
        ]

        with patch('cli.commands.run.time.sleep'):
            result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'COMPLETED'
        assert mock_client.get_workflow_status.call_count == 2

    def test_network_error_exhausts_retries_then_raises(self) -> None:
        """Network errors exhaust retries and propagate the exception."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.side_effect = httpx.ConnectError('Connection refused')

        with patch('cli.commands.run.time.sleep'):
            with pytest.raises((httpx.ConnectError, RuntimeError)):
                run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)


# ---------------------------------------------------------------------------
# .last_run roundtrip verification tests (RUN-03)
# ---------------------------------------------------------------------------


class TestRunCommandLastRun:
    def test_last_run_contains_server_run_id(self, tmp_path: Path) -> None:
        """Polling mode .last_run file contains the server-returned run_id."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(
                run_id='server-run-id-123', workflow_id='wf-123', status='RUNNING'
            ),
        )
        mock_client.get_workflow_status.return_value = MagicMock(
            status='COMPLETED', current_node=None, state={}
        )

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=False,
                no_follow=False,
                working_dir=tmp_path,
            )

        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'server-run-id-123'
