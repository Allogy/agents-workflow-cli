# `workflow status` Command

Check execution state and per-node progress for a workflow run.

## Usage

```
workflow status [--run-id UUID] [--workflow-id UUID] [--json] [--show-outputs]
```

## Options

| Option | Description |
|--------|-------------|
| `--run-id` | Explicit run ID. Uses `.last_run` context when omitted |
| `--workflow-id` | Explicit workflow ID. Requires `--run-id` |
| `--json` | Print raw JSON response and exit |
| `--show-outputs` | Print node output payloads after the status table |

## Run Context Resolution

When `--run-id` and `--workflow-id` are not provided, the command reads context from
`.workflow.last_run` in the current directory (written by `workflow run`):

1. **No flags** — reads both `workflow_id` and `run_id` from `.last_run`
2. **`--run-id` only** — validates that it matches the `run_id` in `.last_run`, then uses both from the file
3. **`--run-id` + `--workflow-id`** — bypasses `.last_run` entirely; both values are used directly
4. **`--workflow-id` without `--run-id`** — error: `--workflow-id` requires `--run-id`

If no `.last_run` exists and no flags are provided, the command exits with an error.

## Output

### Summary Header

```
Workflow: running — 2/4 nodes complete
ID: 939843a8-6257-4475-bfc0-f7d6500d9f00  Run: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Status Table

A Rich table with three columns: **Node**, **Type**, **Status**.

The Node column shows the first 8 characters of the node UUID followed by `...`.
The Type column shows the node's `config_type` (e.g. `LLM`, `HUMAN_REVIEW`, `FILE_UPLOAD`).

#### Node Status Colors

| Status | Color | Meaning |
|--------|-------|---------|
| `COMPLETED` | green | Node finished successfully |
| `RUNNING` | yellow | Node is currently executing |
| `WAITING_FOR_INPUT` | yellow | Node is paused, awaiting user input |
| `WAITING_FOR_REVIEW` | yellow | Node is paused, awaiting human review |
| `WAITING_INPUT` | yellow | Alias for `WAITING_FOR_INPUT` (legacy state) |
| `PAUSED` | yellow | Node is paused (generic) |
| `FAILED` | red | Node or workflow failed |
| `TIMED_OUT` | red | Node or workflow timed out |
| `TIMEOUT` | red | Alias for `TIMED_OUT` (legacy state) |
| `CANCELLED` | red | Workflow was cancelled |
| `PENDING` | dim | Node has not started yet |

Example table output:

```
 Node         Type          Status
 ──────────── ───────────── ───────────────────
 a1b2c3d4...  LLM           COMPLETED
 b2c3d4e5...  FILE_UPLOAD   COMPLETED
 c3d4e5f6...  HUMAN_REVIEW  WAITING_FOR_REVIEW
 d4e5f6a7...  LLM           PENDING
```

### Actionable Hints

After the table, the command prints hints for any paused HITL nodes:

```
Tip: workflow input --node-id <node-id> --data '{...}' (PLAIN_TXT_INPUT)
Tip: workflow review --run-id <run-id> --node-id <node-id> --approve
```

### Node Outputs (`--show-outputs`)

When `--show-outputs` is provided, node output payloads are printed after the hints.
Node slugs are used as display names when available; otherwise the truncated UUID is shown.

```
Node Outputs
  extract-data:
    {
      "vendor": "Acme Corp",
      "total": 1250.00
    }
  upload-invoice:
    {
      "files": ["invoice.pdf"]
    }
```

### Machine-Readable Output (`--json`)

With `--json`, the raw API response is printed as indented JSON and the command exits immediately
(no table, no hints, no node outputs).

## Examples

```bash
# Check status using .last_run context
workflow status

# Explicit run (bypass .last_run)
workflow status --workflow-id 939843a8-6257-4475-bfc0-f7d6500d9f00 \
                --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Machine-readable output
workflow status --json

# Include node output payloads
workflow status --show-outputs

# Both flags together
workflow status --show-outputs --json
```
