"""SSE (Server-Sent Events) line parser for workflow streaming.

Parses SSE data lines from the Temporal workflow execution endpoint.
Only handles 'data:' lines -- ignores comments, event names, and blanks.

AG-UI Protocol event types -- superset of backend AGUIEventType
(backend/src/workflow_framework/src/core/streaming/domain/model/agui_events.py)
plus HITL events sent as payloads by the Temporal workflow engine:
- Lifecycle: RUN_STARTED, RUN_FINISHED, RUN_ERROR
- Steps: STEP_STARTED, STEP_FINISHED, STEP_ERROR
- State: STATE_SNAPSHOT, STATE_DELTA
- Text: TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END
- Tools: TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END, TOOL_CALL_RESULT
- HITL (CLI additions): WAITING_FOR_INPUT, WAITING_FOR_REVIEW, REVIEW_COMPLETE
- Custom: CUSTOM, RAW
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AGUIEventType(str, Enum):
    """AG-UI Protocol event types for the CLI SSE consumer.

    Superset of the backend's AGUIEventType enum. The backend enum defines
    lifecycle, state, step, text, tool, and custom events. This CLI enum adds
    HITL events (WAITING_FOR_INPUT, WAITING_FOR_REVIEW, REVIEW_COMPLETE)
    which the backend sends as event payloads but does not include in its
    AGUIEventType enum.
    """

    # Lifecycle (from backend AGUIEventType)
    RUN_STARTED = 'RUN_STARTED'
    RUN_FINISHED = 'RUN_FINISHED'
    RUN_ERROR = 'RUN_ERROR'

    # State (from backend AGUIEventType)
    STATE_SNAPSHOT = 'STATE_SNAPSHOT'
    STATE_DELTA = 'STATE_DELTA'

    # Steps (from backend AGUIEventType)
    STEP_STARTED = 'STEP_STARTED'
    STEP_FINISHED = 'STEP_FINISHED'
    STEP_ERROR = 'STEP_ERROR'

    # Text messages (from backend AGUIEventType)
    TEXT_MESSAGE_START = 'TEXT_MESSAGE_START'
    TEXT_MESSAGE_CONTENT = 'TEXT_MESSAGE_CONTENT'
    TEXT_MESSAGE_END = 'TEXT_MESSAGE_END'

    # Tool calls (from backend AGUIEventType)
    TOOL_CALL_START = 'TOOL_CALL_START'
    TOOL_CALL_ARGS = 'TOOL_CALL_ARGS'
    TOOL_CALL_END = 'TOOL_CALL_END'
    TOOL_CALL_RESULT = 'TOOL_CALL_RESULT'

    # HITL (CLI additions -- sent by backend as event payloads, not in backend enum)
    WAITING_FOR_INPUT = 'WAITING_FOR_INPUT'
    WAITING_FOR_REVIEW = 'WAITING_FOR_REVIEW'
    REVIEW_COMPLETE = 'REVIEW_COMPLETE'

    # Custom (from backend AGUIEventType)
    CUSTOM = 'CUSTOM'
    RAW = 'RAW'


# Convenience sets for event classification
LIFECYCLE_EVENTS = {AGUIEventType.RUN_STARTED, AGUIEventType.RUN_FINISHED, AGUIEventType.RUN_ERROR}
STEP_EVENTS = {AGUIEventType.STEP_STARTED, AGUIEventType.STEP_FINISHED, AGUIEventType.STEP_ERROR}
STATE_EVENTS = {AGUIEventType.STATE_SNAPSHOT, AGUIEventType.STATE_DELTA}
TEXT_EVENTS = {
    AGUIEventType.TEXT_MESSAGE_START,
    AGUIEventType.TEXT_MESSAGE_CONTENT,
    AGUIEventType.TEXT_MESSAGE_END,
}
TOOL_EVENTS = {
    AGUIEventType.TOOL_CALL_START,
    AGUIEventType.TOOL_CALL_ARGS,
    AGUIEventType.TOOL_CALL_END,
    AGUIEventType.TOOL_CALL_RESULT,
}
HITL_EVENTS = {
    AGUIEventType.WAITING_FOR_INPUT,
    AGUIEventType.WAITING_FOR_REVIEW,
    AGUIEventType.REVIEW_COMPLETE,
}
TERMINAL_EVENTS = {AGUIEventType.RUN_FINISHED, AGUIEventType.RUN_ERROR}


@dataclass
class SSEEvent:
    """A parsed SSE data event."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)


def parse_sse_line(line: str) -> SSEEvent | None:
    """Parse a single SSE line into an SSEEvent.

    Args:
        line: Raw SSE line from the stream.

    Returns:
        SSEEvent if the line is a valid data event, None otherwise.
    """
    line = line.strip()

    if not line or line.startswith(':') or not line.startswith('data:'):
        return None

    json_str = line[len('data:') :].strip()
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    event_type = data.get('type', 'UNKNOWN')
    return SSEEvent(event_type=event_type, data=data)
