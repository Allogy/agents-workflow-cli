"""Unit tests for SSE event parsing."""

from __future__ import annotations

from cli.sse import parse_sse_line


class TestParseSseLine:
    def test_data_line(self) -> None:
        """A 'data: {...}' line returns an SSEEvent."""
        event = parse_sse_line('data: {"type": "RUN_STARTED"}')
        assert event is not None
        assert event.data['type'] == 'RUN_STARTED'

    def test_empty_line_returns_none(self) -> None:
        """Empty lines are ignored."""
        assert parse_sse_line('') is None

    def test_comment_line_returns_none(self) -> None:
        """SSE comment lines (starting with :) are ignored."""
        assert parse_sse_line(': keep-alive') is None

    def test_event_line_returns_none(self) -> None:
        """'event:' lines are ignored (we parse data only)."""
        assert parse_sse_line('event: message') is None

    def test_malformed_json_returns_none(self) -> None:
        """Malformed JSON in data line returns None."""
        assert parse_sse_line('data: not-json') is None

    def test_step_started_event(self) -> None:
        """STEP_STARTED events include node_id."""
        event = parse_sse_line(
            'data: {"type": "STEP_STARTED", "node_id": "abc123", "step_type": "LLM_CALL"}'
        )
        assert event is not None
        assert event.event_type == 'STEP_STARTED'
        assert event.data['node_id'] == 'abc123'

    def test_waiting_for_review_event(self) -> None:
        """WAITING_FOR_REVIEW events are parsed."""
        event = parse_sse_line('data: {"type": "WAITING_FOR_REVIEW", "node_id": "review1"}')
        assert event is not None
        assert event.event_type == 'WAITING_FOR_REVIEW'


def test_agui_event_types_complete():
    """SSE module must define constants for all AG-UI event types."""
    from cli.sse import AGUIEventType

    # Lifecycle
    assert AGUIEventType.RUN_STARTED == 'RUN_STARTED'
    assert AGUIEventType.RUN_FINISHED == 'RUN_FINISHED'
    assert AGUIEventType.RUN_ERROR == 'RUN_ERROR'

    # State
    assert AGUIEventType.STATE_SNAPSHOT == 'STATE_SNAPSHOT'
    assert AGUIEventType.STATE_DELTA == 'STATE_DELTA'

    # Steps
    assert AGUIEventType.STEP_STARTED == 'STEP_STARTED'
    assert AGUIEventType.STEP_FINISHED == 'STEP_FINISHED'
    assert AGUIEventType.STEP_ERROR == 'STEP_ERROR'

    # Text messages
    assert AGUIEventType.TEXT_MESSAGE_START == 'TEXT_MESSAGE_START'
    assert AGUIEventType.TEXT_MESSAGE_CONTENT == 'TEXT_MESSAGE_CONTENT'
    assert AGUIEventType.TEXT_MESSAGE_END == 'TEXT_MESSAGE_END'

    # Tool calls
    assert AGUIEventType.TOOL_CALL_START == 'TOOL_CALL_START'
    assert AGUIEventType.TOOL_CALL_ARGS == 'TOOL_CALL_ARGS'
    assert AGUIEventType.TOOL_CALL_END == 'TOOL_CALL_END'
    assert AGUIEventType.TOOL_CALL_RESULT == 'TOOL_CALL_RESULT'

    # HITL
    assert AGUIEventType.WAITING_FOR_INPUT == 'WAITING_FOR_INPUT'
    assert AGUIEventType.WAITING_FOR_REVIEW == 'WAITING_FOR_REVIEW'
    assert AGUIEventType.REVIEW_COMPLETE == 'REVIEW_COMPLETE'

    # Custom
    assert AGUIEventType.CUSTOM == 'CUSTOM'
    assert AGUIEventType.RAW == 'RAW'


def test_parse_state_delta_event():
    """STATE_DELTA events should be parsed with node output data."""
    from cli.sse import parse_sse_line

    line = 'data: {"type": "STATE_DELTA", "delta": [{"op": "add", "path": "/node_outputs/abc", "value": {"text": "Hello"}}]}'
    event = parse_sse_line(line)
    assert event is not None
    assert event.event_type == 'STATE_DELTA'
    assert 'delta' in event.data


def test_parse_custom_sse_pausing_event():
    """CUSTOM events with name SSE_PAUSING should be parsed."""
    from cli.sse import parse_sse_line

    line = 'data: {"type": "CUSTOM", "name": "SSE_PAUSING", "value": {}}'
    event = parse_sse_line(line)
    assert event is not None
    assert event.event_type == 'CUSTOM'
    assert event.data.get('name') == 'SSE_PAUSING'
