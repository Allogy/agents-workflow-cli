"""Unit tests for workflow run command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from cli.commands.run import (
    format_sse_event,
    parse_input_arg,
    resolve_workflow_id,
    run_polling,
    run_streaming,
)
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
