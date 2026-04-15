# Temporal Execution Guide

Complete reference for running and interacting with workflows via the Temporal engine from the CLI.

## Overview

The `workflow run` command executes a workflow through the Temporal durable execution engine. Temporal
handles retries, timeouts, and state persistence, while the CLI provides four execution modes to
match different use cases — from interactive development to CI/CD pipelines.

Related command docs:
- [`run-command.md`](run-command.md) — command flags reference
- [`sse-events.md`](sse-events.md) — full SSE event payload reference
- [`input-command.md`](input-command.md) — submitting HITL input
- [`review-command.md`](review-command.md) — submitting HITL review decisions
- [`status-command.md`](status-command.md) — checking execution status

---

## Execution Lifecycle

A workflow execution passes through a defined sequence of states:

```
workflow run <id>
       │
       ▼
   [PENDING]  ← Temporal worker picks up the task
       │
       ▼
   [RUNNING]  ← Nodes are executing sequentially/in parallel
       │
       ├─── HITL node reached?
       │         │
       │         ▼
       │  [WAITING_FOR_INPUT]   ← Paused, awaiting user data
       │  [WAITING_FOR_REVIEW]  ← Paused, awaiting approval/rejection
       │         │
       │         ▼
       │     (submit via `workflow input` or `workflow review`)
       │         │
       │         └──────────► [RUNNING]  ← Resumes
       │
       ├─── All nodes complete ──► [COMPLETED]
       ├─── Node raises error  ──► [FAILED]
       ├─── Manual cancel      ──► [CANCELLED]
       └─── Wall-clock timeout ──► [TIMED_OUT]
```

The `.workflow.last_run` context file is written at the moment the run is started (before polling
or streaming begins), so subsequent HITL commands can pick up the run automatically.

---

## Execution Statuses

| Status | Terminal | Description |
|--------|----------|-------------|
| `PENDING` | No | Queued; waiting for a Temporal worker to pick up the task |
| `RUNNING` | No | At least one node is currently executing |
| `PAUSED` | No | Execution paused by a domain-layer signal (not a HITL gate) |
| `WAITING_FOR_INPUT` | No | Paused at an input node; user must submit data to resume |
| `WAITING_FOR_REVIEW` | No | Paused at a review node; user must approve/reject/revise to resume |
| `COMPLETED` | **Yes** | All nodes finished successfully |
| `FAILED` | **Yes** | A node raised an unrecoverable error |
| `CANCELLED` | **Yes** | Execution was cancelled by a user or system signal |
| `TIMED_OUT` | **Yes** | Exceeded the configured wall-clock timeout |

The CLI treats `COMPLETED` and `RUN_FINISHED` (SSE) as success. The following statuses produce
exit code 1: `FAILED`, `CANCELLED`, `TIMED_OUT`, `RUN_ERROR`.

> **Implementation note:** The backend exposes status from three layers (core domain, ORM, and
> Temporal engine) which can return different casing. The CLI normalises all status values to
> uppercase before comparison. The legacy `TIMEOUT` string (old backend versions) is also
> recognised as equivalent to `TIMED_OUT`.

---

## Execution Modes

### 1. Polling (default)

The default mode. Starts the workflow via `POST /v2/workflows/{id}/start/temporal`, then polls
`GET /v2/workflows/{id}/status` every 2 seconds until a terminal status or HITL gate is reached.

```bash
workflow run 939843a8-6257-4475-bfc0-f7d6500d9f00
workflow run "Invoice Processing"
workflow run my-workflow --input '{"question": "What is AI?"}'
```

**What the CLI prints:**
- Run ID and mode banner
- Each node as it is visited: `[N/total] node-slug ... running`
- A HITL hint when paused: `Next: workflow input --node-id <id> --data '{...}'`
- Final status with node outputs on completion

**Retry behaviour:** Network errors (`ConnectError`, `TimeoutException`, `ReadError`) are retried
up to 3 times with exponential backoff (1s, 2s, 4s). If all retries are exhausted the CLI exits
with the original error.

---

### 2. SSE Streaming (`--stream`)

Opens a single long-lived HTTP connection to `POST /v2/workflows/{id}/run/temporal` with
`Accept: text/event-stream`. Events are rendered in real time as they arrive.

```bash
workflow run my-workflow --stream
workflow run my-workflow --stream --verbose        # multi-line payload excerpts
workflow run my-workflow --stream --input @data.json
```

**What the CLI prints:**
- Run ID and mode banner
- One line per SSE event (compact format): `[HH:MM:SS] EVENT_TYPE node-slug (details)`
- A run summary table (node, type, status, duration) when `RUN_FINISHED` is received
- A HITL hint when the stream pauses: `Next: workflow input ...` / `Next: workflow review --approve`

**Retry behaviour:** Transient HTTP errors (502, 503) trigger up to 2 retries with a 1.5s delay.
Connection and read errors are also retried. Non-retryable HTTP errors are raised immediately.

If the stream closes without a terminal event the CLI prints a warning and exits — use
`workflow status` to check the current state.

---

### 3. Interactive (`--stream --interactive`)

Extends SSE streaming with inline HITL prompts. When the stream pauses at a HITL gate the CLI
prompts the user for input or a review decision inline, submits it, then resumes monitoring via
polling until the next gate or terminal state.

```bash
workflow run my-workflow --stream --interactive
workflow run my-workflow --stream --interactive --verbose
```

**Requirements:**
- `--stream` must also be set
- Must be running in a TTY (not piped or redirected)

**HITL loop:**
1. SSE stream runs until `WAITING_FOR_INPUT` or `WAITING_FOR_REVIEW`
2. CLI prompts the user (see [HITL Workflows](#hitl-workflows) below)
3. Submission is sent via `workflow input` / `workflow review` API calls
4. CLI polls until the next HITL gate or terminal state, then repeats
5. On `RUN_FINISHED` a full run summary table is printed

Pressing `Ctrl+C` or answering the cancellation prompt exits the interactive loop without
terminating the running workflow.

---

### 4. Fire-and-Forget (`--no-follow`)

Starts the workflow and exits immediately without following progress. The run ID and a status-check
hint are printed.

```bash
workflow run my-workflow --no-follow
```

Useful for CI/CD pipelines where a separate step checks the result via `workflow status`.

> **Note:** `--input` data is **not** auto-submitted in `--no-follow` mode. Submit it manually
> with `workflow input <run-id> --data '{...}'` after the workflow reaches `WAITING_FOR_INPUT`.

---

## HITL Workflows

Human-in-the-loop (HITL) nodes pause workflow execution and wait for an external signal. There are
two categories: input nodes (provide data) and review nodes (approve or reject).

### Input Nodes

Three node types produce a `WAITING_FOR_INPUT` status:

| Node Type | Config Type | What the user provides |
|-----------|-------------|------------------------|
| Plain text input | `PLAIN_TXT_INPUT` | Free-form text string |
| Structured input | `STRUCTURED_INPUT` | JSON object matching the node's schema |
| File upload | `FILE_UPLOAD` | One or more local files (PDF, DOCX, etc.) |

**Submitting input manually (polling mode):**

```bash
# After the CLI prints: "Next: workflow input --node-id <node-id> --data '{...}'"
workflow input --node-id <node-id> --data '{"answer": "Paris"}'

# From .workflow.last_run context (no run-id needed)
workflow input --node-id <node-id> --data '{"answer": "Paris"}'

# File upload
workflow input --node-id <node-id> --files /path/to/document.pdf
```

**Auto-submitting with `--input` (polling and streaming modes):**

If `--input` data is supplied and the first HITL gate is `WAITING_FOR_INPUT`, the CLI
auto-submits the data and resumes automatically:

```bash
workflow run my-workflow --input '{"question": "What is AI?"}' --stream
```

The auto-submit fires once and then clears — subsequent HITL gates still require manual
interaction unless `--interactive` is used.

**Interactive mode prompts:**

In `--stream --interactive` mode the CLI prompts inline:
- Text/JSON nodes: `Enter input for <node-slug> [<step_type>]:`
- File upload nodes: `Enter file path(s) for <node-slug> (comma-separated):`

Files are uploaded via `POST /v2/workflows/{id}/upload` before the input signal is sent.

---

### Review Nodes

A `HUMAN_REVIEW` node produces a `WAITING_FOR_REVIEW` status. The reviewer must choose one of
three decisions:

| Decision | Description |
|----------|-------------|
| `approve` | Accept the output; execution continues to the next node |
| `reject` | Reject the output; workflow is typically terminated or routed to an error path |
| `revise` | Return for revision; workflow routes back to a prior node for rework |

**Submitting a review manually:**

```bash
workflow review --approve
workflow review --reject --feedback "Incorrect calculation"
workflow review --revise --feedback "Please recalculate with updated rates"
```

**Interactive mode prompts:**

In `--stream --interactive` mode the CLI prompts:

```
Review required for <node-slug> [HUMAN_REVIEW]
Decision [approve/reject/revise]: approve
Feedback (optional):
```

---

## Node Types Reference

All node types that can appear in a workflow execution, with their config type identifier and role:

| Config Type | Category | Purpose |
|-------------|----------|---------|
| `AGENT` | Agent | LLM-powered autonomous agent with tool use |
| `RAG_AGENT` | Agent | Retrieval-augmented agent with document context |
| `LLM_CALL` | LLM | Single LLM call with prompt template |
| `STRUCTURED_OUTPUT` | LLM | LLM call that returns a validated JSON schema |
| `RETRIEVE` | Data | Vector/semantic retrieval from a knowledge base |
| `PLAIN_TXT_INPUT` | Input (HITL) | Pauses for a plain text string from the user |
| `STRUCTURED_INPUT` | Input (HITL) | Pauses for a JSON object from the user |
| `FILE_UPLOAD` | Input (HITL) | Pauses for one or more file uploads from the user |
| `HUMAN_REVIEW` | Review (HITL) | Pauses for approve/reject/revise from a reviewer |
| `SELECTION` | Control | Routes execution based on a conditional value |
| `ITERATOR` | Control | Fan-out: runs downstream nodes once per item in a list |
| `DOCUMENT_EXTRACTION` | Processing | Extracts structured data from uploaded documents |

---

## Timeout Behaviour

The CLI enforces a wall-clock timeout on polling and streaming operations. When the timeout is
reached the CLI prints a warning and exits with code 1 — **the workflow itself continues running
in Temporal** unless the backend's own Temporal workflow timeout fires independently.

| Precedence | Source | Default |
|------------|--------|---------|
| 1 (highest) | `--timeout <seconds>` CLI flag | — |
| 2 | `WORKFLOW_RUN_TIMEOUT` environment variable | — |
| 3 (lowest) | Built-in default | 1800s (30 minutes) |

```bash
# Override to 10 minutes
WORKFLOW_RUN_TIMEOUT=600 workflow run my-workflow

# Check status after CLI timeout
workflow status <run-id>
```

---

## Context File (`.workflow.last_run`)

Every `workflow run` invocation writes a `.workflow.last_run` YAML file to the current working
directory immediately after the run is started. This file stores the execution context so that
subsequent `workflow input`, `workflow review`, and `workflow status` commands can operate without
requiring the run ID to be passed explicitly.

**File format:**

```yaml
# Auto-generated by workflow CLI. Do not edit manually.
workflow_id: 939843a8-6257-4475-bfc0-f7d6500d9f00
run_id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
instance: https://api.sb.allogy.com
started_at: 2026-02-25T10:30:00+00:00
```

| Field | Type | Description |
|-------|------|-------------|
| `workflow_id` | UUID | The workflow definition that was executed |
| `run_id` | string | The Temporal run ID returned by the platform |
| `instance` | string | API host URL used for this run |
| `started_at` | ISO 8601 datetime | Wall-clock time when the run was started |

The file is overwritten on each new `workflow run` invocation. It is safe to commit or ignore
in version control — treat it like a local `.env` file.

---

## Troubleshooting

### Workflow stuck in RUNNING

The workflow may be waiting for a Temporal worker, or a node is taking longer than expected.

```bash
# Check current status
workflow status <run-id>

# Or use .workflow.last_run context
workflow status
```

If the status shows `WAITING_FOR_INPUT` or `WAITING_FOR_REVIEW` but the run command has exited,
submit the required interaction:

```bash
workflow input --node-id <node-id> --data '{"key": "value"}'
workflow review --approve
```

### Stream disconnected before completion

The CLI prints a warning when the SSE stream closes without a terminal event:

```
Warning: Stream interrupted before completion. Use workflow status to check current state.
```

This is typically a transient network issue. The workflow continues running in Temporal. Check
status and resume monitoring:

```bash
workflow status
```

To follow progress after a disconnect, poll manually:

```bash
watch -n 5 workflow status
```

### CLI timeout reached but workflow is still running

The CLI exits with code 1 when the wall-clock timeout is reached, but the Temporal workflow
continues executing. Increase the timeout or check status separately:

```bash
# Check if the workflow finished after CLI timed out
workflow status <run-id>

# Run with a longer timeout next time
WORKFLOW_RUN_TIMEOUT=7200 workflow run my-workflow  # 2 hours
```

### 502 / 503 errors on stream connect

Transient gateway errors are retried automatically (up to 2 retries, 1.5s apart). If the errors
persist, check whether the backend API is healthy:

```bash
# Verify API connectivity
curl -H "X-API-Key: $WORKFLOW_API_KEY" $WORKFLOW_HOST/health
```

### Workflow resolved to wrong UUID

Identifier resolution searches in this order: UUID passthrough → lockfile → API name search.
If a name matches multiple workflows the first API result wins. Prefer UUIDs for unambiguous
targeting in scripts:

```bash
# Explicit UUID — no ambiguity
workflow run 939843a8-6257-4475-bfc0-f7d6500d9f00

# List workflows to find the UUID
workflow list
```
