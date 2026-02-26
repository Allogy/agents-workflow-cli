"""SSE (Server-Sent Events) line parser for workflow streaming.

Parses SSE data lines from the Temporal workflow execution endpoint.
Only handles 'data:' lines -- ignores comments, event names, and blanks.

Event types from the backend:
- RUN_STARTED, RUN_FINISHED, RUN_ERROR
- STEP_STARTED, STEP_FINISHED, STEP_ERROR
- WAITING_FOR_INPUT, WAITING_FOR_REVIEW, REVIEW_COMPLETE
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


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
