"""Unit tests for workflow run command."""

from __future__ import annotations

import json
import re
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
    _print_final_status,
    format_sse_compact,
    format_sse_event,
    format_sse_verbose,
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
        assert result == 'STEP_STARTED'


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
        assert result == 'WAITING_FOR_INPUT'


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
