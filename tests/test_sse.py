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
