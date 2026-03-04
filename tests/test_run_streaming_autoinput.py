"""Test auto-submit in streaming mode."""

from __future__ import annotations

from unittest.mock import MagicMock

from cli.client import SubmitInputResponse
from cli.commands.run import run_streaming


def _make_sse_lines_with_input_pause() -> list[str]:
    """SSE lines that pause at WAITING_FOR_INPUT."""
    return [
        'event: RUN_STARTED',
        'data: {"type": "RUN_STARTED", "run_id": "r1"}',
        '',
        'event: STEP_STARTED',
        'data: {"type": "STEP_STARTED", "node_id": "node-1", "step_name": "input-node", "step_type": "plain_txt_input"}',
        '',
        'event: WAITING_FOR_INPUT',
        'data: {"type": "WAITING_FOR_INPUT", "node_id": "node-1", "step_name": "input-node", "step_type": "plain_txt_input"}',
        '',
    ]


def test_streaming_returns_waiting_without_autoinput():
    """Without pending_input, streaming stops at WAITING_FOR_INPUT."""
    lines = iter(_make_sse_lines_with_input_pause())
    result = run_streaming(lines, total_nodes=1)
    assert result.final_event.upper() == 'WAITING_FOR_INPUT'


def test_streaming_autoinput_calls_submit(monkeypatch):
    """With pending_input and submit_fn, auto-submits on WAITING_FOR_INPUT."""
    submit_fn = MagicMock(
        return_value=SubmitInputResponse(
            workflow_id='wf-1',
            node_id='node-1',
            status='submitted',
            message='ok',
        )
    )

    lines = iter(_make_sse_lines_with_input_pause())
    result = run_streaming(
        lines,
        total_nodes=1,
        pending_input={'text': 'hello'},
        submit_input_fn=submit_fn,
    )

    submit_fn.assert_called_once_with('node-1', {'text': 'hello'})
    assert result.final_event.upper() == 'WAITING_FOR_INPUT'
