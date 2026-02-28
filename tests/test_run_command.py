"""Unit tests for workflow run command."""

from __future__ import annotations

import json
import re
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from cli.commands.run import (
    NodeResult,
    StreamResult,
    _format_duration_ms,
    _get_display_name,
    _poll_until_next_event,
    _print_final_status,
    _print_summary_table,
    _run_interactive,
    format_sse_compact,
    format_sse_event,
    format_sse_verbose,
    format_unknown_event,
    parse_file_input,
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
# File input parsing tests
# ---------------------------------------------------------------------------


class TestParseFileInput:
    """Tests for file:// prefix parsing in --input."""

    def test_single_file(self, tmp_path: Path) -> None:
        """Single file:// path returns one-element list."""
        f = tmp_path / 'report.pdf'
        f.write_bytes(b'fake pdf')
        result = parse_file_input(f'file://{f}')
        assert result == [f]

    def test_multiple_files(self, tmp_path: Path) -> None:
        """Comma-separated file:// paths return multiple paths."""
        f1 = tmp_path / 'a.pdf'
        f1.write_bytes(b'pdf')
        f2 = tmp_path / 'b.docx'
        f2.write_bytes(b'docx')
        result = parse_file_input(f'file://{f1},file://{f2}')
        assert result == [f1, f2]

    def test_nonexistent_raises(self) -> None:
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_file_input('file:///nonexistent/file.pdf')

    def test_not_a_file_raises(self, tmp_path: Path) -> None:
        """Directory path raises ValueError."""
        d = tmp_path / 'subdir'
        d.mkdir()
        with pytest.raises(ValueError, match='Not a file'):
            parse_file_input(f'file://{d}')

    def test_without_prefix_still_works(self, tmp_path: Path) -> None:
        """Path without file:// prefix is also accepted."""
        f = tmp_path / 'data.csv'
        f.write_bytes(b'a,b')
        result = parse_file_input(str(f))
        assert result == [f]


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
        with pytest.raises(ValueError, match='No workflow found matching'):
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

    def test_detects_hitl_from_state_execution_status(self) -> None:
        """Polling detects WAITING_FOR_INPUT via state.execution_status when top-level is RUNNING."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='RUNNING',
            current_node='input_node',
            state={'execution_status': 'WAITING_FOR_INPUT', 'current_node_id': 'input_node'},
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'WAITING_FOR_INPUT'

    def test_detects_review_from_state_execution_status(self) -> None:
        """Polling detects WAITING_FOR_REVIEW via state.execution_status when top-level is RUNNING."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='RUNNING',
            current_node='review_node',
            state={'execution_status': 'WAITING_FOR_REVIEW', 'current_node_id': 'review_node'},
        )

        result = run_polling(mock_client, 'wf-id', 'run-id', poll_interval=0)
        assert result == 'WAITING_FOR_REVIEW'


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
        """Streaming returns StreamResult with final_event from events."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "STEP_STARTED", "node_id": "n1"}',
            'data: {"type": "STEP_FINISHED", "node_id": "n1"}',
            'data: {"type": "RUN_FINISHED"}',
        ]
        result = run_streaming(iter(lines))
        assert result.final_event == 'RUN_FINISHED'

    def test_hitl_gate_stops_stream(self) -> None:
        """Streaming returns on WAITING_FOR_REVIEW."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "WAITING_FOR_REVIEW", "node_id": "r1"}',
        ]
        result = run_streaming(iter(lines))
        assert result.final_event == 'WAITING_FOR_REVIEW'


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
        assert result.final_event == 'run_finished'


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
        with patch('cli.main.load_config') as mock_load_config:
            mock_load_config.return_value = _make_mock_config()
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


# ---------------------------------------------------------------------------
# Compact SSE format tests (RUN-04)
# ---------------------------------------------------------------------------


class TestFormatSseCompact:
    def test_compact_format_has_timestamp(self) -> None:
        """Compact format includes [HH:MM:SS] timestamp prefix."""
        event = SSEEvent(
            event_type='STEP_FINISHED',
            data={'type': 'STEP_FINISHED', 'node_id': 'process-invoice'},
        )
        line = format_sse_compact(event)
        # Check the raw Rich markup string for the timestamp pattern
        assert re.search(r'\[\d{2}:\d{2}:\d{2}\]', line)
        assert 'STEP_FINISHED' in line
        assert 'process-invoice' in line

    def test_compact_success_events_green(self) -> None:
        """Success events (RUN_FINISHED) use green color markup."""
        event = SSEEvent(
            event_type='RUN_FINISHED',
            data={'type': 'RUN_FINISHED'},
        )
        line = format_sse_compact(event)
        assert '[green]' in line

    def test_compact_hitl_events_yellow(self) -> None:
        """HITL events (WAITING_FOR_REVIEW) use yellow color markup."""
        event = SSEEvent(
            event_type='WAITING_FOR_REVIEW',
            data={'type': 'WAITING_FOR_REVIEW', 'node_id': 'review-1'},
        )
        line = format_sse_compact(event)
        assert '[yellow]' in line

    def test_compact_error_events_red(self) -> None:
        """Error events (STEP_ERROR) use red color markup."""
        event = SSEEvent(
            event_type='STEP_ERROR',
            data={'type': 'STEP_ERROR', 'node_id': 'bad-node', 'error': 'timeout'},
        )
        line = format_sse_compact(event)
        assert '[red]' in line


# ---------------------------------------------------------------------------
# Verbose SSE format tests (RUN-04)
# ---------------------------------------------------------------------------


class TestFormatSseVerbose:
    def test_verbose_shows_payload_excerpt(self) -> None:
        """Verbose format includes payload data excerpt."""
        event = SSEEvent(
            event_type='STEP_FINISHED',
            data={
                'type': 'STEP_FINISHED',
                'node_id': 'abc',
                'output': {'result': 'success'},
            },
        )
        line = format_sse_verbose(event)
        assert 'output' in line

    def test_verbose_includes_timestamp(self) -> None:
        """Verbose format includes [HH:MM:SS] timestamp."""
        event = SSEEvent(
            event_type='STEP_FINISHED',
            data={'type': 'STEP_FINISHED', 'node_id': 'abc'},
        )
        line = format_sse_verbose(event)
        # Check the raw Rich markup string for the timestamp pattern
        assert re.search(r'\[\d{2}:\d{2}:\d{2}\]', line)


# ---------------------------------------------------------------------------
# No-color tests (RUN-04)
# ---------------------------------------------------------------------------


class TestRunStreamingNoColor:
    def test_no_color_strips_markup(self) -> None:
        """When Console(no_color=True) is used, output has no ANSI color codes."""
        buf = StringIO()
        no_color_console = Console(file=buf, no_color=True)

        event = SSEEvent(
            event_type='STEP_FINISHED',
            data={'type': 'STEP_FINISHED', 'node_id': 'node-1'},
        )
        line = format_sse_compact(event)
        no_color_console.print(line)
        output = buf.getvalue()

        # Should NOT contain ANSI escape codes AND no color markup words
        assert '\x1b[' not in output and 'green' not in output


class TestRunPollingNoColor:
    def test_print_final_status_respects_no_color(self) -> None:
        """_print_final_status uses output_console, not a module-level Console."""
        buf = StringIO()
        no_color_console = Console(file=buf, no_color=True)

        _print_final_status('COMPLETED', 'run-123', output_console=no_color_console)
        output = buf.getvalue()

        # Must contain the completion message
        assert 'Workflow completed' in output
        # Must NOT contain ANSI escape codes
        assert '\x1b[' not in output


# ---------------------------------------------------------------------------
# Interrupted stream detection tests
# ---------------------------------------------------------------------------


class TestRunStreamingInterrupted:
    def test_interrupted_stream_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Stream ending without terminal event prints a warning."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "STEP_STARTED", "node_id": "n1"}',
            # No terminal event -- iterator ends abruptly
        ]
        result = run_streaming(iter(lines))
        captured = capsys.readouterr()
        output = captured.out.lower()
        assert 'interrupt' in output or 'status' in output
        assert result.final_event == 'STEP_STARTED'


# ---------------------------------------------------------------------------
# HITL hint tests
# ---------------------------------------------------------------------------


class TestRunStreamingHitlHint:
    def test_waiting_for_input_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """WAITING_FOR_INPUT event prints actionable hint with workflow input command."""
        lines = [
            'data: {"type": "RUN_STARTED"}',
            'data: {"type": "WAITING_FOR_INPUT", "node_id": "input-1"}',
        ]
        result = run_streaming(iter(lines))
        captured = capsys.readouterr()
        output = captured.out.lower()
        assert 'workflow input' in output or 'node-id' in output
        assert result.final_event == 'WAITING_FOR_INPUT'


# ---------------------------------------------------------------------------
# End-to-end run_command tests (02-03)
# ---------------------------------------------------------------------------


class TestRunCommandE2E:
    """End-to-end tests exercising the full run_command() flow with mocked client.

    These tests cover all four acceptance criteria from RUN-01 through RUN-04.
    """

    # -- RUN-01: Execute by ID with --input --------------------------------

    def test_run_e2e_by_id_with_input(self, tmp_path: Path) -> None:
        """RUN-01: Execute by UUID with --input parses JSON and starts workflow."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='run-123', workflow_id='wf-123', status='RUNNING'),
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
                input_data='{"question": "What is AI?"}',
                working_dir=tmp_path,
            )

        # start_workflow_temporal called with parsed inputs dict
        mock_client.start_workflow_temporal.assert_called_once()
        call_kwargs = mock_client.start_workflow_temporal.call_args
        assert call_kwargs[1]['inputs'] == {'question': 'What is AI?'}

        # .last_run file written
        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'run-123'

    def test_run_e2e_invalid_input_raises_value_error(self, tmp_path: Path) -> None:
        """RUN-01: Invalid JSON input raises ValueError (main.py routes to exit 2)."""
        with pytest.raises(ValueError, match='Invalid JSON'):
            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data='not-json',
                working_dir=tmp_path,
            )

    # -- RUN-02: Execute by name -------------------------------------------

    def test_run_e2e_by_name_resolves_and_executes(self, tmp_path: Path) -> None:
        """RUN-02: Name identifier is resolved to UUID via API before execution."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='name-run-id', workflow_id='abc-123', status='RUNNING'),
        )
        mock_client.get_workflow_status.return_value = MagicMock(
            status='COMPLETED', current_node=None, state={}
        )

        # Mock name resolution: one workflow with name "Invoice Processing"
        mock_wf = MagicMock()
        mock_wf.id = UUID('11111111-2222-3333-4444-555555555555')
        mock_client.list_workflows.return_value = [mock_wf]
        mock_metadata = MagicMock()
        mock_metadata.name = 'Invoice Processing'
        mock_client.get_metadata.return_value = mock_metadata

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='Invoice Processing',
                input_data=None,
                working_dir=tmp_path,
            )

        # start_workflow_temporal should have been called with the resolved UUID
        call_args = mock_client.start_workflow_temporal.call_args
        assert '11111111-2222-3333-4444-555555555555' in call_args[0][0]

    def test_run_e2e_name_not_found_raises_with_suggestions(self, tmp_path: Path) -> None:
        """RUN-02: Misspelled name raises ValueError with 'Did you mean' suggestions."""
        mock_client = _make_mock_client()

        # Mock two workflows with known names
        workflows = []
        for i, _name in enumerate(['Invoice Processing', 'Order Flow']):
            wf = MagicMock()
            wf.id = UUID(f'1111111{i}-2222-3333-4444-555555555555')
            workflows.append(wf)
        mock_client.list_workflows.return_value = workflows

        def _get_metadata(wf_id: Any) -> MagicMock:
            names = ['Invoice Processing', 'Order Flow']
            for j, wf in enumerate(workflows):
                if wf.id == wf_id:
                    meta = MagicMock()
                    meta.name = names[j]
                    return meta
            raise Exception('Not found')

        mock_client.get_metadata.side_effect = _get_metadata

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(ValueError, match='Did you mean'):
                run_command(
                    config=_make_mock_config(),
                    identifier='Invoce',
                    input_data=None,
                    working_dir=tmp_path,
                )

    # -- RUN-03: .last_run written -----------------------------------------

    def test_run_e2e_last_run_written_on_success(self, tmp_path: Path) -> None:
        """RUN-03: .last_run file exists after successful polling run with correct run_id."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='success-run-id', workflow_id='wf-123', status='RUNNING'),
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
                working_dir=tmp_path,
            )

        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'success-run-id'

    def test_run_e2e_last_run_written_on_failure(self, tmp_path: Path) -> None:
        """RUN-03: .last_run file exists even after a FAILED run (written before polling)."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='failed-run-id', workflow_id='wf-123', status='RUNNING'),
        )
        mock_client.get_workflow_status.return_value = MagicMock(
            status='FAILED', current_node=None, state={}
        )

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(SystemExit) as exc_info:
                run_command(
                    config=_make_mock_config(),
                    identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                    input_data=None,
                    working_dir=tmp_path,
                )
            assert exc_info.value.code == 1

        # .last_run should still exist -- written before polling starts
        ctx = load_last_run(tmp_path)
        assert ctx is not None
        assert ctx.run_id == 'failed-run-id'


# ---------------------------------------------------------------------------
# Enhanced formatter tests (06-01)
# ---------------------------------------------------------------------------


class TestGetDisplayName:
    def test_node_slug_preferred_over_node_id(self) -> None:
        """node_slug is preferred over node_id when both present."""
        event = SSEEvent('STEP_STARTED', {'node_slug': 'extract-data', 'node_id': 'abc-123'})
        assert _get_display_name(event) == 'extract-data'

    def test_step_name_fallback(self) -> None:
        """step_name used when node_slug absent."""
        event = SSEEvent('STEP_STARTED', {'step_name': 'extract-data', 'node_id': 'abc-123'})
        assert _get_display_name(event) == 'extract-data'

    def test_node_id_fallback(self) -> None:
        """node_id used when both node_slug and step_name absent."""
        event = SSEEvent('STEP_STARTED', {'node_id': 'abc-123'})
        assert _get_display_name(event) == 'abc-123'

    def test_empty_data(self) -> None:
        """Empty data returns empty string."""
        event = SSEEvent('STEP_STARTED', {})
        assert _get_display_name(event) == ''


class TestFormatDurationMs:
    def test_zero_ms(self) -> None:
        assert _format_duration_ms(0) == '0ms'

    def test_sub_second(self) -> None:
        assert _format_duration_ms(523) == '523ms'

    def test_just_under_1000(self) -> None:
        assert _format_duration_ms(999) == '999ms'

    def test_exactly_1000(self) -> None:
        assert _format_duration_ms(1000) == '1s'

    def test_integer_division(self) -> None:
        """1523ms -> 1s (integer division to seconds)."""
        assert _format_duration_ms(1523) == '1s'

    def test_multi_minute(self) -> None:
        """90000ms -> 1m 30s."""
        assert _format_duration_ms(90000) == '1m 30s'


class TestFormatUnknownEvent:
    def test_short_payload_renders_complete(self) -> None:
        """Short payload is rendered without truncation."""
        event = SSEEvent('CUSTOM_EVENT', {'field': 'val'})
        output = format_unknown_event(event)
        assert 'CUSTOM_EVENT' in output
        assert '"field"' in output

    def test_long_payload_truncated(self) -> None:
        """Long payload (>100 chars JSON) is truncated with ellipsis."""
        data = {'key_' + str(i): 'value_' + str(i) * 10 for i in range(20)}
        event = SSEEvent('CUSTOM_EVENT', data)
        output = format_unknown_event(event)
        assert output.endswith('...[/dim]')

    def test_output_wrapped_in_dim(self) -> None:
        """Output is wrapped in [dim] markup."""
        event = SSEEvent('CUSTOM_EVENT', {'field': 'val'})
        output = format_unknown_event(event)
        assert output.startswith('[dim]')
        assert output.endswith('[/dim]')


class TestEnhancedCompactFormat:
    def test_step_started_shows_step_type(self) -> None:
        """STEP_STARTED shows step_type in parentheses."""
        event = SSEEvent(
            'STEP_STARTED',
            {'node_slug': 'extract-data', 'step_type': 'llm_call', 'node_id': 'abc-123'},
        )
        output = format_sse_compact(event)
        assert 'extract-data' in output
        assert 'llm_call' in output

    def test_step_finished_shows_duration(self) -> None:
        """STEP_FINISHED shows formatted duration."""
        event = SSEEvent(
            'STEP_FINISHED',
            {'node_slug': 'extract-data', 'duration_ms': 1523, 'node_id': 'abc-123'},
        )
        output = format_sse_compact(event)
        assert 'extract-data' in output
        assert '1s' in output

    def test_step_error_shows_error_and_type(self) -> None:
        """STEP_ERROR shows error message and error_type when present."""
        event = SSEEvent(
            'STEP_ERROR',
            {
                'node_slug': 'extract-data',
                'error': 'LLM failed',
                'error_type': 'ApplicationError',
                'node_id': 'abc-123',
            },
        )
        output = format_sse_compact(event)
        assert 'LLM failed' in output
        assert 'ApplicationError' in output

    def test_step_error_without_error_type(self) -> None:
        """STEP_ERROR without error_type shows only error, no 'None' in output."""
        event = SSEEvent(
            'STEP_ERROR',
            {'node_slug': 'extract-data', 'error': 'LLM failed', 'node_id': 'abc-123'},
        )
        output = format_sse_compact(event)
        assert 'LLM failed' in output
        assert 'None' not in output

    def test_run_error_shows_error_and_type(self) -> None:
        """RUN_ERROR shows error message and error_type when present."""
        event = SSEEvent(
            'RUN_ERROR',
            {'error': 'Workflow failed', 'error_type': 'ApplicationError'},
        )
        output = format_sse_compact(event)
        assert 'Workflow failed' in output
        assert 'ApplicationError' in output

    def test_run_error_without_error_type(self) -> None:
        """RUN_ERROR without error_type shows only error."""
        event = SSEEvent(
            'RUN_ERROR',
            {'error': 'Workflow failed'},
        )
        output = format_sse_compact(event)
        assert 'Workflow failed' in output
        assert 'None' not in output


class TestEnhancedVerboseFormat:
    def test_step_error_multiline_expansion(self) -> None:
        """STEP_ERROR expands to multi-line with Error: and Type: fields."""
        event = SSEEvent(
            'STEP_ERROR',
            {
                'node_slug': 'extract-data',
                'error': 'LLM failed',
                'error_type': 'ApplicationError',
                'node_id': 'abc-123',
            },
        )
        output = format_sse_verbose(event)
        lines = output.split('\n')
        # Should have Error: and Type: on separate lines
        error_lines = [line for line in lines if 'Error:' in line]
        type_lines = [line for line in lines if 'Type:' in line and 'step_type' not in line.lower()]
        assert len(error_lines) >= 1
        assert len(type_lines) >= 1

    def test_step_error_with_code(self) -> None:
        """STEP_ERROR with code shows Code: field."""
        event = SSEEvent(
            'STEP_ERROR',
            {
                'node_slug': 'extract-data',
                'error': 'fail',
                'error_type': 'AppError',
                'code': 'RATE_LIMIT',
                'node_id': 'abc-123',
            },
        )
        output = format_sse_verbose(event)
        assert 'Code:' in output
        assert 'RATE_LIMIT' in output

    def test_run_error_multiline_expansion(self) -> None:
        """RUN_ERROR expands to multi-line with Error: and Type: fields."""
        event = SSEEvent(
            'RUN_ERROR',
            {
                'error': 'Workflow failed',
                'error_type': 'ApplicationError',
            },
        )
        output = format_sse_verbose(event)
        lines = output.split('\n')
        error_lines = [line for line in lines if 'Error:' in line]
        type_lines = [line for line in lines if 'Type:' in line]
        assert len(error_lines) >= 1
        assert len(type_lines) >= 1


class TestDataclasses:
    def test_node_result_all_fields(self) -> None:
        """NodeResult can be created with all fields."""
        nr = NodeResult(
            node_id='abc-123',
            display_name='extract-data',
            step_type='llm_call',
            status='finished',
            duration_ms=1523,
        )
        assert nr.node_id == 'abc-123'
        assert nr.display_name == 'extract-data'
        assert nr.step_type == 'llm_call'
        assert nr.status == 'finished'
        assert nr.duration_ms == 1523

    def test_node_result_optional_duration(self) -> None:
        """NodeResult works with duration_ms=None (default)."""
        nr = NodeResult(
            node_id='abc-123',
            display_name='extract-data',
            step_type='llm_call',
            status='started',
        )
        assert nr.duration_ms is None

    def test_stream_result_empty_nodes(self) -> None:
        """StreamResult with final_event and empty nodes list."""
        sr = StreamResult(final_event='RUN_FINISHED')
        assert sr.final_event == 'RUN_FINISHED'
        assert sr.nodes == []

    def test_stream_result_with_nodes(self) -> None:
        """StreamResult with populated nodes list."""
        nr = NodeResult(
            node_id='abc-123',
            display_name='extract-data',
            step_type='llm_call',
            status='finished',
            duration_ms=500,
        )
        sr = StreamResult(final_event='RUN_FINISHED', nodes=[nr])
        assert len(sr.nodes) == 1
        assert sr.nodes[0].display_name == 'extract-data'

    def test_run_e2e_last_run_written_on_streaming(self, tmp_path: Path) -> None:
        """RUN-03: .last_run file exists after streaming mode run."""
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

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                working_dir=tmp_path,
            )

        ctx = load_last_run(tmp_path)
        assert ctx is not None

    # -- RUN-04: Streaming mode --------------------------------------------

    def test_run_e2e_streaming_exits_0_on_hitl_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RUN-04: Streaming mode exits 0 on WAITING_FOR_REVIEW gate with HITL hint."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                'data: {"type": "RUN_STARTED"}',
                'data: {"type": "WAITING_FOR_REVIEW", "node_id": "review-1"}',
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            # Should NOT raise (exit 0 for HITL gate)
            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                working_dir=tmp_path,
            )

        # The HITL hint should have been printed
        captured = capsys.readouterr()
        output = captured.out.lower()
        assert 'review' in output

    # -- Timeout message quality -------------------------------------------

    def test_timeout_message_includes_workflow_status_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Timeout message includes elapsed time and 'workflow status' hint."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='timeout-run-id', workflow_id='wf-123', status='RUNNING'),
        )
        mock_client.get_workflow_status.return_value = MagicMock(
            status='RUNNING', current_node='node1', state={}
        )

        with (
            patch('cli.commands.run.WorkflowClient') as MockClient,
            patch('cli.commands.run.get_run_timeout', return_value=0.01),
            patch('cli.commands.run.time.sleep'),
        ):
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(SystemExit) as exc_info:
                run_command(
                    config=_make_mock_config(),
                    identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                    input_data=None,
                    working_dir=tmp_path,
                )
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        output = captured.out.lower()
        assert 'workflow status' in output or 'status' in output


# ---------------------------------------------------------------------------
# Audit gap-fill tests (02-03, Task 2)
# ---------------------------------------------------------------------------


class TestRunCommandAuditGaps:
    """Tests filling remaining coverage gaps found during acceptance-criteria audit."""

    def test_polling_mode_prints_run_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RUN-01 gap: Console output contains 'Run ID' during polling execution."""
        mock_client = _make_mock_client(
            start_resp=MagicMock(run_id='visible-run-id', workflow_id='wf-123', status='RUNNING'),
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
                working_dir=tmp_path,
            )

        captured = capsys.readouterr()
        output = captured.out
        assert 'Run ID' in output
        assert 'visible-run-id' in output

    def test_streaming_mode_prints_run_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """RUN-04 gap: Streaming mode console output contains 'Run ID'."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                'data: {"type": "RUN_STARTED"}',
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

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                working_dir=tmp_path,
            )

        captured = capsys.readouterr()
        output = captured.out
        assert 'Run ID' in output

    def test_no_follow_mode_does_not_poll(self, tmp_path: Path) -> None:
        """--no-follow starts workflow and returns without polling."""
        mock_client = _make_mock_client()

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                no_follow=True,
                working_dir=tmp_path,
            )

        # get_workflow_status should never have been called (no polling)
        mock_client.get_workflow_status.assert_not_called()


# ---------------------------------------------------------------------------
# StreamResult integration tests (06-02)
# ---------------------------------------------------------------------------


def _sse_line(event_type: str, **data: Any) -> str:
    """Helper to create SSE data lines for tests."""
    payload = {'type': event_type, **data}
    return f'data: {json.dumps(payload)}'


class TestRunStreamingStreamResult:
    """Tests verifying run_streaming returns StreamResult with correct fields."""

    def test_basic_return_type(self) -> None:
        """Feed a minimal stream, assert return is StreamResult."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines))
        assert isinstance(result, StreamResult)
        assert result.final_event == 'RUN_FINISHED'
        assert isinstance(result.nodes, list)

    def test_node_accumulation(self) -> None:
        """Feed STEP_STARTED + STEP_FINISHED, assert NodeResult accumulated."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line(
                'STEP_STARTED',
                node_slug='extract-data',
                step_type='llm_call',
                node_id='abc',
            ),
            _sse_line('STEP_FINISHED', node_id='abc', node_slug='extract-data', duration_ms=1500),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines))
        assert len(result.nodes) == 1
        assert result.nodes[0].display_name == 'extract-data'
        assert result.nodes[0].step_type == 'llm_call'
        assert result.nodes[0].status == 'finished'
        assert result.nodes[0].duration_ms == 1500

    def test_error_node(self) -> None:
        """Feed STEP_STARTED + STEP_ERROR + RUN_ERROR, assert node has status='error'."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line(
                'STEP_STARTED',
                node_slug='bad-node',
                step_type='api_call',
                node_id='err1',
            ),
            _sse_line('STEP_ERROR', node_id='err1', node_slug='bad-node', error='timeout'),
            _sse_line('RUN_ERROR', error='Workflow failed'),
        ]
        result = run_streaming(iter(lines))
        assert result.final_event == 'RUN_ERROR'
        assert len(result.nodes) == 1
        assert result.nodes[0].status == 'error'
        assert result.nodes[0].display_name == 'bad-node'

    def test_multiple_nodes_accumulated(self) -> None:
        """Multiple nodes are accumulated in order."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_slug='node-a', node_id='a1', step_type='llm_call'),
            _sse_line('STEP_FINISHED', node_id='a1', node_slug='node-a', duration_ms=500),
            _sse_line('STEP_STARTED', node_slug='node-b', node_id='b1', step_type='api_call'),
            _sse_line('STEP_FINISHED', node_id='b1', node_slug='node-b', duration_ms=1200),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines))
        assert len(result.nodes) == 2
        names = [n.display_name for n in result.nodes]
        assert 'node-a' in names
        assert 'node-b' in names


class TestClientSideDurationFallback:
    """Tests for client-side duration fallback when backend omits duration_ms."""

    def test_duration_from_backend(self) -> None:
        """When backend provides duration_ms, it is used directly."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_id='n1', node_slug='node-1'),
            _sse_line('STEP_FINISHED', node_id='n1', duration_ms=1000),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines))
        assert result.nodes[0].duration_ms == 1000

    def test_client_side_fallback(self) -> None:
        """When backend omits duration_ms, client-side monotonic clock is used."""
        # Calls: start_time, RUN_STARTED timeout, STEP_STARTED record, STEP_STARTED timeout,
        #        STEP_FINISHED fallback, STEP_FINISHED timeout, RUN_FINISHED (terminal)
        monotonic_values = iter([10.0, 10.0, 10.0, 10.0, 12.5, 12.5])

        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_id='n1', node_slug='node-1'),
            _sse_line('STEP_FINISHED', node_id='n1'),
            _sse_line('RUN_FINISHED'),
        ]
        with patch('cli.commands.run.time.monotonic', side_effect=monotonic_values):
            result = run_streaming(iter(lines), max_timeout_seconds=3600)
        assert result.nodes[0].duration_ms == 2500

    def test_error_node_client_side_fallback(self) -> None:
        """STEP_ERROR also uses client-side fallback when backend omits duration_ms."""
        # Calls: start_time, RUN_STARTED timeout, STEP_STARTED record, STEP_STARTED timeout,
        #        STEP_ERROR fallback, STEP_ERROR timeout, RUN_ERROR (terminal)
        monotonic_values = iter([10.0, 10.0, 10.0, 10.0, 13.0, 13.0])

        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_id='n1', node_slug='node-1'),
            _sse_line('STEP_ERROR', node_id='n1', error='failed'),
            _sse_line('RUN_ERROR', error='Workflow failed'),
        ]
        with patch('cli.commands.run.time.monotonic', side_effect=monotonic_values):
            result = run_streaming(iter(lines), max_timeout_seconds=3600)
        assert result.nodes[0].status == 'error'
        assert result.nodes[0].duration_ms == 3000


class TestSummaryTable:
    """Tests for the Rich summary table on RUN_FINISHED."""

    def test_summary_printed_on_run_finished(self) -> None:
        """Summary table appears in output when stream ends with RUN_FINISHED."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_id='n1', node_slug='extract'),
            _sse_line('STEP_FINISHED', node_id='n1', duration_ms=500),
            _sse_line('RUN_FINISHED'),
        ]
        run_streaming(iter(lines), output_console=console)
        output = buf.getvalue()
        assert 'Run Summary' in output

    def test_summary_not_printed_on_run_error(self) -> None:
        """Summary table does NOT appear when stream ends with RUN_ERROR."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('STEP_STARTED', node_id='n1', node_slug='extract'),
            _sse_line('STEP_ERROR', node_id='n1', error='boom'),
            _sse_line('RUN_ERROR', error='Workflow failed'),
        ]
        run_streaming(iter(lines), output_console=console)
        output = buf.getvalue()
        assert 'Run Summary' not in output

    def test_print_summary_table_directly(self) -> None:
        """_print_summary_table renders node data in table format."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        nodes = [
            NodeResult(
                node_id='abc',
                display_name='extract-data',
                step_type='llm_call',
                status='finished',
                duration_ms=1523,
            ),
            NodeResult(
                node_id='def',
                display_name='transform',
                step_type='api_call',
                status='error',
                duration_ms=500,
            ),
        ]
        _print_summary_table(nodes, console)
        output = buf.getvalue()
        assert 'Run Summary' in output
        assert 'extract-data' in output
        assert 'transform' in output

    def test_print_summary_table_empty_list(self) -> None:
        """_print_summary_table with empty list produces no output."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        _print_summary_table([], console)
        output = buf.getvalue()
        assert output == ''

    def test_summary_table_shows_duration(self) -> None:
        """Summary table formats duration correctly."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        nodes = [
            NodeResult(
                node_id='a',
                display_name='fast-node',
                step_type='llm_call',
                status='finished',
                duration_ms=250,
            ),
        ]
        _print_summary_table(nodes, console)
        output = buf.getvalue()
        assert '250ms' in output

    def test_summary_table_shows_dash_for_no_duration(self) -> None:
        """Summary table shows '-' when duration_ms is None."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        nodes = [
            NodeResult(
                node_id='a',
                display_name='no-duration',
                step_type='llm_call',
                status='started',
            ),
        ]
        _print_summary_table(nodes, console)
        output = buf.getvalue()
        assert 'no-duration' in output


class TestUnknownEventInStreaming:
    """Tests for unknown SSE event handling during streaming."""

    def test_unknown_event_renders_dim(self) -> None:
        """Unknown event renders as dim text with event type."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('CUSTOM_THING', payload='hello'),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines), output_console=console)
        output = buf.getvalue()
        assert 'CUSTOM_THING' in output
        assert result.final_event == 'RUN_FINISHED'

    def test_unknown_event_does_not_crash(self) -> None:
        """Stream continues past unknown events to reach terminal event."""
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('MYSTERY_EVENT', data_field='value'),
            _sse_line('STEP_STARTED', node_id='n1'),
            _sse_line('STEP_FINISHED', node_id='n1', duration_ms=100),
            _sse_line('RUN_FINISHED'),
        ]
        result = run_streaming(iter(lines))
        assert result.final_event == 'RUN_FINISHED'
        assert len(result.nodes) == 1

    def test_multiple_unknown_events_all_appear(self) -> None:
        """Multiple unknown events all appear in output (no deduplication)."""
        buf = StringIO()
        console = Console(file=buf, no_color=True)
        lines = [
            _sse_line('RUN_STARTED'),
            _sse_line('CUSTOM_A', info='first'),
            _sse_line('CUSTOM_B', info='second'),
            _sse_line('CUSTOM_C', info='third'),
            _sse_line('RUN_FINISHED'),
        ]
        run_streaming(iter(lines), output_console=console)
        output = buf.getvalue()
        assert 'CUSTOM_A' in output
        assert 'CUSTOM_B' in output
        assert 'CUSTOM_C' in output


class TestRunCommandWithStreamResult:
    """Tests that run_command handles StreamResult correctly."""

    def test_streaming_run_error_exits_with_code_1(self, tmp_path: Path) -> None:
        """run_command with stream=True exits with code 1 on RUN_ERROR."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                _sse_line('RUN_STARTED'),
                _sse_line('STEP_STARTED', node_id='n1'),
                _sse_line('STEP_ERROR', node_id='n1', error='boom'),
                _sse_line('RUN_ERROR', error='Workflow failed'),
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(SystemExit) as exc_info:
                run_command(
                    config=_make_mock_config(),
                    identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                    input_data=None,
                    stream=True,
                    working_dir=tmp_path,
                )
            assert exc_info.value.code == 1

    def test_streaming_run_finished_exits_cleanly(self, tmp_path: Path) -> None:
        """run_command with stream=True exits cleanly on RUN_FINISHED."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                _sse_line('RUN_STARTED'),
                _sse_line('STEP_STARTED', node_id='n1'),
                _sse_line('STEP_FINISHED', node_id='n1', duration_ms=500),
                _sse_line('RUN_FINISHED'),
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with patch('cli.commands.run.WorkflowClient') as MockClient:
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            # Should NOT raise
            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                working_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Interactive loop tests (07-02)
# ---------------------------------------------------------------------------


class TestRunInteractive:
    """Integration tests for the interactive HITL dispatch in run_command."""

    def test_interactive_dispatches_on_hitl_event(self, tmp_path: Path) -> None:
        """run_command with interactive=True dispatches to _run_interactive on HITL event."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                'data: {"type": "RUN_STARTED"}',
                'data: {"type": "WAITING_FOR_INPUT", "node_id": "input-1"}',
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch('cli.commands.run.WorkflowClient') as MockClient,
            patch(
                'cli.commands.run._run_interactive',
                return_value=StreamResult(final_event='RUN_FINISHED', nodes=[]),
            ) as mock_interactive,
            patch.object(sys.stdin, 'isatty', return_value=True),
        ):
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                interactive=True,
                working_dir=tmp_path,
            )

        mock_interactive.assert_called_once()

    def test_non_interactive_skips_interactive_loop(self, tmp_path: Path) -> None:
        """run_command with interactive=False does NOT call _run_interactive."""
        mock_client = _make_mock_client()
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(
            [
                'data: {"type": "RUN_STARTED"}',
                'data: {"type": "WAITING_FOR_INPUT", "node_id": "input-1"}',
            ]
        )
        mock_client.stream_workflow_temporal.return_value.__enter__ = MagicMock(
            return_value=mock_response
        )
        mock_client.stream_workflow_temporal.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch('cli.commands.run.WorkflowClient') as MockClient,
            patch('cli.commands.run._run_interactive') as mock_interactive,
        ):
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                interactive=False,
                working_dir=tmp_path,
            )

        mock_interactive.assert_not_called()

    def test_interactive_terminal_event_skips_loop(self, tmp_path: Path) -> None:
        """Interactive mode with terminal event (RUN_FINISHED) does NOT dispatch to loop."""
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

        with (
            patch('cli.commands.run.WorkflowClient') as MockClient,
            patch('cli.commands.run._run_interactive') as mock_interactive,
            patch.object(sys.stdin, 'isatty', return_value=True),
        ):
            MockClient.from_config.return_value.__enter__ = MagicMock(return_value=mock_client)
            MockClient.from_config.return_value.__exit__ = MagicMock(return_value=False)

            run_command(
                config=_make_mock_config(),
                identifier='939843a8-6257-4475-bfc0-f7d6500d9f00',
                input_data=None,
                stream=True,
                interactive=True,
                working_dir=tmp_path,
            )

        mock_interactive.assert_not_called()


class TestPollUntilNextEvent:
    """Tests for the _poll_until_next_event function."""

    def test_returns_on_terminal_status(self) -> None:
        """Returns immediately on COMPLETED status."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='COMPLETED', current_node=None, state={}
        )

        with patch('cli.commands.run.time.sleep'):
            result = _poll_until_next_event(mock_client, 'wf-id', 'run-id')
        assert result == 'COMPLETED'

    def test_returns_on_hitl_status(self) -> None:
        """Returns on WAITING_FOR_INPUT after polling through RUNNING."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.side_effect = [
            MagicMock(status='RUNNING', current_node='node-1', state={}),
            MagicMock(status='WAITING_FOR_INPUT', current_node='input-1', state={}),
        ]

        with patch('cli.commands.run.time.sleep'):
            result = _poll_until_next_event(mock_client, 'wf-id', 'run-id')
        assert result == 'WAITING_FOR_INPUT'

    def test_timeout_exits(self) -> None:
        """Exceeding max_timeout_seconds causes sys.exit(1)."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='RUNNING', current_node='node-1', state={}
        )

        with (
            patch('cli.commands.run.time.sleep'),
            pytest.raises(SystemExit) as exc_info,
        ):
            _poll_until_next_event(mock_client, 'wf-id', 'run-id', max_timeout_seconds=0)
        assert exc_info.value.code == 1

    def test_prints_node_transitions(self) -> None:
        """Node changes are printed as processing status lines."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.side_effect = [
            MagicMock(status='RUNNING', current_node='node-a', state={}),
            MagicMock(status='RUNNING', current_node='node-b', state={}),
            MagicMock(status='COMPLETED', current_node=None, state={}),
        ]

        buf = StringIO()
        console = Console(file=buf, no_color=True)
        with patch('cli.commands.run.time.sleep'):
            _poll_until_next_event(mock_client, 'wf-id', 'run-id', output_console=console)
        output = buf.getvalue()
        assert 'node-a' in output
        assert 'node-b' in output


class TestRunInteractiveLoop:
    """Unit tests for the _run_interactive orchestration function."""

    def test_terminal_event_returns_immediately(self) -> None:
        """Terminal initial_result (RUN_FINISHED) returns without prompting."""
        mock_client = MagicMock()
        initial = StreamResult(final_event='RUN_FINISHED', nodes=[])

        result = _run_interactive(mock_client, 'wf-id', 'run-id', initial)
        assert result.final_event == 'RUN_FINISHED'
        # No API calls made
        mock_client.get_workflow_status.assert_not_called()

    def test_input_prompt_submit_and_poll_to_completion(self) -> None:
        """WAITING_FOR_INPUT -> prompt -> submit -> poll -> COMPLETED."""
        mock_client = MagicMock()
        # Status API returns waiting node info
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='input-1',
            state={'waiting_input_node_id': 'input-1'},
        )
        # submit_input succeeds
        mock_client.submit_input.return_value = MagicMock(status='ok')

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_input',
                return_value={'key': 'value'},
            ),
            patch(
                'cli.commands.run._poll_until_next_event',
                return_value='COMPLETED',
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'input-1': ('my-input-node', 'user_input')},
            )

        assert result.final_event == 'COMPLETED'
        mock_client.submit_input.assert_called_once()

    def test_review_prompt_submit_and_poll_to_completion(self) -> None:
        """WAITING_FOR_REVIEW -> prompt -> submit -> poll -> COMPLETED."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_REVIEW',
            current_node='review-1',
            state={'review_node_id': 'review-1'},
        )
        mock_client.submit_review.return_value = MagicMock(status='ok')

        initial = StreamResult(final_event='WAITING_FOR_REVIEW', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_review',
                return_value=('approve', None),
            ),
            patch(
                'cli.commands.run._poll_until_next_event',
                return_value='COMPLETED',
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'review-1': ('my-review-node', 'human_review')},
            )

        assert result.final_event == 'COMPLETED'
        mock_client.submit_review.assert_called_once_with(
            'wf-id', run_id='run-id', decision='approve', feedback=None
        )

    def test_user_cancel_returns_current_result(self) -> None:
        """User cancelling prompt (None) returns the current result."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='input-1',
            state={'waiting_input_node_id': 'input-1'},
        )

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch('cli.interactive.prompt_for_input', return_value=None),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(mock_client, 'wf-id', 'run-id', initial)

        assert result.final_event == 'WAITING_FOR_INPUT'
        mock_client.submit_input.assert_not_called()

    def test_multiple_hitl_gates_in_sequence(self) -> None:
        """Handles INPUT -> REVIEW -> COMPLETED in sequence."""
        mock_client = MagicMock()
        # First poll: waiting for input info; second poll: waiting for review info
        mock_client.get_workflow_status.side_effect = [
            MagicMock(
                status='WAITING_FOR_INPUT',
                current_node='input-1',
                state={'waiting_input_node_id': 'input-1'},
            ),
            MagicMock(
                status='WAITING_FOR_REVIEW',
                current_node='review-1',
                state={'review_node_id': 'review-1'},
            ),
        ]
        mock_client.submit_input.return_value = MagicMock(status='ok')
        mock_client.submit_review.return_value = MagicMock(status='ok')

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_input',
                return_value={'data': 'test'},
            ),
            patch(
                'cli.interactive.prompt_for_review',
                return_value=('approve', None),
            ),
            patch(
                'cli.commands.run._poll_until_next_event',
                side_effect=['WAITING_FOR_REVIEW', 'COMPLETED'],
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={
                    'input-1': ('my-input', 'user_input'),
                    'review-1': ('my-review', 'human_review'),
                },
            )

        assert result.final_event == 'COMPLETED'
        mock_client.submit_input.assert_called_once()
        mock_client.submit_review.assert_called_once()

    def test_api_failure_with_retry_success(self) -> None:
        """API failure on first submit, retry succeeds."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='input-1',
            state={'waiting_input_node_id': 'input-1'},
        )
        mock_client.submit_input.side_effect = [
            Exception('Network error'),
            MagicMock(status='ok'),
        ]

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_input',
                return_value={'key': 'value'},
            ),
            patch('cli.commands.run.Confirm.ask', return_value=True),
            patch(
                'cli.commands.run._poll_until_next_event',
                return_value='COMPLETED',
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(mock_client, 'wf-id', 'run-id', initial)

        assert result.final_event == 'COMPLETED'
        assert mock_client.submit_input.call_count == 2

    def test_api_failure_retry_declined_returns(self) -> None:
        """API failure with user declining retry returns current result."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='input-1',
            state={'waiting_input_node_id': 'input-1'},
        )
        mock_client.submit_input.side_effect = Exception('Network error')

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_input',
                return_value={'key': 'value'},
            ),
            patch('cli.commands.run.Confirm.ask', return_value=False),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(mock_client, 'wf-id', 'run-id', initial)

        assert result.final_event == 'WAITING_FOR_INPUT'

    # ------------------------------------------------------------------
    # File upload branch tests
    # ------------------------------------------------------------------

    def test_file_upload_node_uses_file_prompt(self, tmp_path: Path) -> None:
        """FILE_UPLOAD step_type triggers prompt_for_file_upload instead of prompt_for_input."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='upload-1',
            state={'waiting_input_node_id': 'upload-1'},
        )
        mock_client.submit_input.return_value = MagicMock(status='ok')

        # Create a temporary file for upload
        test_file = tmp_path / 'doc.pdf'
        test_file.write_bytes(b'%PDF-fake')

        mock_client.upload_file.return_value = MagicMock(
            file_id='file-abc',
            filename='doc.pdf',
            s3_uri='s3://bucket/doc.pdf',
            file_size=9,
            content_type='application/pdf',
        )

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_file_upload',
                return_value=[test_file],
            ),
            patch('cli.interactive.prompt_for_input') as mock_text_prompt,
            patch(
                'cli.commands.run._poll_until_next_event',
                return_value='COMPLETED',
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'upload-1': ('my-upload-node', 'FILE_UPLOAD')},
            )

        assert result.final_event == 'COMPLETED'
        # prompt_for_file_upload was used, not prompt_for_input
        mock_text_prompt.assert_not_called()
        mock_client.upload_file.assert_called_once()
        mock_client.submit_input.assert_called_once()

    def test_file_upload_submits_correct_payload(self, tmp_path: Path) -> None:
        """Verify the input_data has {'files': [...], 'type': 'fileUpload'} format."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='upload-1',
            state={'waiting_input_node_id': 'upload-1'},
        )
        mock_client.submit_input.return_value = MagicMock(status='ok')

        file_a = tmp_path / 'a.pdf'
        file_a.write_bytes(b'pdf-a')
        file_b = tmp_path / 'b.docx'
        file_b.write_bytes(b'docx-b')

        # Two files -> two upload responses
        mock_client.upload_file.side_effect = [
            MagicMock(
                file_id='id-a',
                filename='a.pdf',
                s3_uri='s3://bucket/a.pdf',
                file_size=5,
                content_type='application/pdf',
            ),
            MagicMock(
                file_id='id-b',
                filename='b.docx',
                s3_uri='s3://bucket/b.docx',
                file_size=6,
                content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ),
        ]

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_file_upload',
                return_value=[file_a, file_b],
            ),
            patch(
                'cli.commands.run._poll_until_next_event',
                return_value='COMPLETED',
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'upload-1': ('my-upload-node', 'FILE_UPLOAD')},
            )

        assert result.final_event == 'COMPLETED'
        assert mock_client.upload_file.call_count == 2

        # Verify submit_input was called with the correct payload
        submit_call = mock_client.submit_input.call_args
        input_data = submit_call.kwargs.get('input_data') or submit_call[1].get('input_data')
        assert input_data['type'] == 'fileUpload'
        assert len(input_data['files']) == 2
        assert input_data['files'][0]['file_id'] == 'id-a'
        assert input_data['files'][0]['name'] == 'a.pdf'
        assert input_data['files'][0]['s3_uri'] == 's3://bucket/a.pdf'
        assert input_data['files'][1]['file_id'] == 'id-b'
        assert input_data['files'][1]['name'] == 'b.docx'

    def test_file_upload_cancelled_returns_result(self) -> None:
        """prompt_for_file_upload returns None -> function returns early."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='upload-1',
            state={'waiting_input_node_id': 'upload-1'},
        )

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch('cli.interactive.prompt_for_file_upload', return_value=None),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'upload-1': ('my-upload-node', 'FILE_UPLOAD')},
            )

        assert result.final_event == 'WAITING_FOR_INPUT'
        mock_client.upload_file.assert_not_called()
        mock_client.submit_input.assert_not_called()

    def test_file_upload_upload_failure_returns_result(self, tmp_path: Path) -> None:
        """Upload failure on one file returns the current result early."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='upload-1',
            state={'waiting_input_node_id': 'upload-1'},
        )

        test_file = tmp_path / 'doc.pdf'
        test_file.write_bytes(b'%PDF-fake')

        mock_client.upload_file.side_effect = Exception('Upload failed: 500')

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_file_upload',
                return_value=[test_file],
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'upload-1': ('my-upload-node', 'FILE_UPLOAD')},
            )

        assert result.final_event == 'WAITING_FOR_INPUT'
        mock_client.submit_input.assert_not_called()

    def test_file_upload_submit_failure_returns_result(self, tmp_path: Path) -> None:
        """Submit failure after successful upload returns the current result."""
        mock_client = MagicMock()
        mock_client.get_workflow_status.return_value = MagicMock(
            status='WAITING_FOR_INPUT',
            current_node='upload-1',
            state={'waiting_input_node_id': 'upload-1'},
        )

        test_file = tmp_path / 'doc.pdf'
        test_file.write_bytes(b'%PDF-fake')

        mock_client.upload_file.return_value = MagicMock(
            file_id='file-abc',
            filename='doc.pdf',
            s3_uri='s3://bucket/doc.pdf',
            file_size=9,
            content_type='application/pdf',
        )
        mock_client.submit_input.side_effect = Exception('Submit failed: 500')

        initial = StreamResult(final_event='WAITING_FOR_INPUT', nodes=[])

        with (
            patch(
                'cli.interactive.prompt_for_file_upload',
                return_value=[test_file],
            ),
            patch('cli.commands.run.time.sleep'),
        ):
            result = _run_interactive(
                mock_client,
                'wf-id',
                'run-id',
                initial,
                node_map={'upload-1': ('my-upload-node', 'FILE_UPLOAD')},
            )

        assert result.final_event == 'WAITING_FOR_INPUT'
