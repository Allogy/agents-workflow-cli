# SSE Events Reference

Server-Sent Events (SSE) are the streaming protocol used during `workflow run` execution. The backend sends a continuous stream of `data:` lines over the Temporal workflow execution endpoint. Each line carries a JSON payload with a `type` field identifying the event.

The CLI parses these events in `src/cli/sse.py` and renders them in `src/cli/commands/run.py`.

## Event Categories

Events are grouped into six categories plus two catch-all types:

| Category | Events |
|----------|--------|
| Lifecycle | `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR` |
| Step | `STEP_STARTED`, `STEP_FINISHED`, `STEP_ERROR` |
| State | `STATE_SNAPSHOT`, `STATE_DELTA` |
| Text Message | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| Tool Call | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT` |
| HITL | `WAITING_FOR_INPUT`, `WAITING_FOR_REVIEW`, `REVIEW_COMPLETE` |
| Custom | `CUSTOM`, `RAW` |

## Terminal vs Non-Terminal

Two event types signal that the stream has ended and no further events will follow:

| Event | Terminal |
|-------|----------|
| `RUN_FINISHED` | yes — successful completion |
| `RUN_ERROR` | yes — failed execution |
| All others | no |

The CLI uses this classification to know when to stop reading the stream and display the run summary.

## Lifecycle Events

### RUN_STARTED

Emitted once at the very beginning of a run, before any nodes execute.

| Field | Type | Description |
|-------|------|-------------|
| `thread_id` | string | Identifier for the conversation thread |
| `run_id` | string | Unique identifier for this execution |

### RUN_FINISHED

Terminal event. Emitted when all nodes have completed successfully.

No additional payload fields beyond `type`.

### RUN_ERROR

Terminal event. Emitted when the run fails at the top level (outside any specific node).

| Field | Type | Description |
|-------|------|-------------|
| `error` | string | Human-readable error message |
| `error_type` | string | Exception class name or error category |
| `code` | string | Machine-readable error code |

## Step Events

Step events track individual node executions within the workflow.

### STEP_STARTED

Emitted when a node begins executing.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | UUID of the node |
| `node_slug` | string | Human-readable slug identifier |
| `step_type` | string | Node type (e.g., `llm`, `retrieval`, `condition`) |
| `step_index` | integer | Zero-based position in execution order |

### STEP_FINISHED

Emitted when a node completes successfully.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | UUID of the node |
| `node_slug` | string | Human-readable slug identifier |
| `duration_ms` | integer | Execution time in milliseconds |
| `output` | object | Node output payload |

### STEP_ERROR

Emitted when a node fails. The run may or may not continue depending on workflow configuration.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | UUID of the node |
| `error` | string | Human-readable error message |
| `error_type` | string | Exception class name or error category |

## State Events

State events carry the evolving workflow state — the accumulated outputs of all completed nodes.

### STATE_SNAPSHOT

Emitted to deliver a full point-in-time snapshot of workflow state. Typically sent at the start of a run to establish baseline state.

| Field | Type | Description |
|-------|------|-------------|
| `snapshot` | object | Full state object |
| `snapshot.node_outputs` | object | Map of node ID to output payload |

### STATE_DELTA

Emitted incrementally as node outputs change. Uses [JSON Patch (RFC 6902)](https://datatracker.ietf.org/doc/html/rfc6902) operations.

| Field | Type | Description |
|-------|------|-------------|
| `delta` | array | Array of JSON Patch operation objects |

Each operation object has the standard JSON Patch fields (`op`, `path`, `value`). Node output patches use paths of the form `/node_outputs/<node_id>`. The value at that path is the node's output object, which may include fields like `text`, `content`, or `response`.

Example:
```json
{
  "type": "STATE_DELTA",
  "delta": [
    {
      "op": "add",
      "path": "/node_outputs/abc-123",
      "value": { "text": "The answer is 42." }
    }
  ]
}
```

## Text Message Events

Text message events carry streamed LLM output, chunked for real-time display.

### TEXT_MESSAGE_START

Signals the beginning of a new text message stream.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | string | Unique identifier for this message |

### TEXT_MESSAGE_CONTENT

Carries an incremental chunk of text content.

| Field | Type | Description |
|-------|------|-------------|
| `content` | string | Text chunk (may be a single token or multiple words) |

### TEXT_MESSAGE_END

Signals that the text message stream is complete.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | string | Identifier matching the corresponding `TEXT_MESSAGE_START` |

## Tool Call Events

Tool call events track LLM tool/function invocations during execution.

### TOOL_CALL_START

Emitted when the LLM initiates a tool call.

| Field | Type | Description |
|-------|------|-------------|
| `tool_call_id` | string | Unique identifier for this tool call |
| `name` | string | Name of the tool being called |

### TOOL_CALL_ARGS

Carries the arguments for the tool call, streamed incrementally.

| Field | Type | Description |
|-------|------|-------------|
| `args` | string | Partial or complete JSON-encoded arguments |

### TOOL_CALL_END

Signals that argument streaming for a tool call is complete.

| Field | Type | Description |
|-------|------|-------------|
| `tool_call_id` | string | Identifier matching the corresponding `TOOL_CALL_START` |

### TOOL_CALL_RESULT

Carries the result returned from the tool execution.

| Field | Type | Description |
|-------|------|-------------|
| `tool_call_id` | string | Identifier matching the corresponding `TOOL_CALL_START` |
| `result` | any | Tool return value |

## HITL Events

Human-in-the-loop (HITL) events are sent by the Temporal workflow engine as event payloads. They are not part of the backend's core `AGUIEventType` enum but are handled by the CLI as first-class events.

### WAITING_FOR_INPUT

The workflow is paused and requires a human to provide input before execution can continue.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | UUID of the node waiting for input |

Use `workflow input` to supply the required value and resume execution.

### WAITING_FOR_REVIEW

The workflow is paused and requires a human to approve or reject a result before execution can continue.

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | UUID of the node waiting for review |

Use `workflow review` to approve or reject and resume execution.

### REVIEW_COMPLETE

Emitted when a review decision has been recorded and the workflow resumes.

No additional payload fields beyond `type`.

## Custom Events

### CUSTOM

A named custom event sent by workflow logic. The payload varies by event name.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Event name |
| `value` | any | Event-specific payload |

Notable custom event names:

| Name | Description |
|------|-------------|
| `SSE_PAUSING` | The workflow is entering a paused state (e.g., before a HITL pause) |

### RAW

A pass-through event for unstructured or legacy payloads. The payload varies.

## CLI Display Colors

The `workflow run` command color-codes events by type using Rich markup:

| Color | Events |
|-------|--------|
| green | `RUN_STARTED`, `STEP_FINISHED`, `RUN_FINISHED`, `REVIEW_COMPLETE` |
| blue | `STEP_STARTED`, `STATE_SNAPSHOT` |
| cyan | `STATE_DELTA`, `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| magenta | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT` |
| yellow | `WAITING_FOR_REVIEW`, `WAITING_FOR_INPUT` |
| red | `STEP_ERROR`, `RUN_ERROR` |
| dim | `CUSTOM`, `RAW`, unknown event types |

## Verbose Output

Pass `--verbose` to `workflow run` to enable multi-line output for each event. In verbose mode:

- Each event is printed on its own block with timestamp, event type, and node name on the first line.
- `step_type` is shown on a second line when present.
- Error events (`STEP_ERROR`, `RUN_ERROR`) expand to multiple lines: `Error`, `Type`, `Code`, and `Traceback` when available.
- Known non-error events show a payload excerpt (up to 6 lines) with non-identity fields.
- Unknown event types show the full raw JSON payload.

Without `--verbose` (the default), each event is formatted as a single compact line:

```
[HH:MM:SS] EVENT_TYPE node-name (details)
```
