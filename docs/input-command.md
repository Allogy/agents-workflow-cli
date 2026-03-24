# `workflow input` Command

Submit data to a paused INPUT node in a running workflow execution.

## Usage

```
workflow input --node-id UUID [--data JSON|@file] [--run-id UUID] [--workflow-id UUID] [--json]
```

## Options

| Option | Description |
|--------|-------------|
| `--node-id` | **(Required)** ID of the INPUT node to submit data to |
| `--data` | Input payload as an inline JSON string or `@filepath`. Defaults to `{}` when omitted |
| `--run-id` | Explicit run ID. Uses `.last_run` context when omitted |
| `--workflow-id` | Explicit workflow ID. Requires `--run-id` |
| `--json` | Print raw JSON response and exit |

## Run Context Resolution

The command resolves `workflow_id` and `run_id` using the same logic as `workflow status`:

1. **No `--run-id` / `--workflow-id`** — reads both from `.workflow.last_run` in the current directory
2. **`--run-id` only** — validates it matches the run ID stored in `.last_run`, then uses both from the file
3. **`--run-id` + `--workflow-id`** — bypasses `.last_run` entirely
4. **`--workflow-id` without `--run-id`** — error: `--workflow-id` requires `--run-id`

## Input Data Formats

### Inline JSON string

```bash
workflow input --node-id <uuid> --data '{"text": "Hello, world"}'
```

### File reference (`@filepath`)

```bash
workflow input --node-id <uuid> --data @payload.json
```

The file must contain valid JSON. If the file does not exist, the command exits with an error.

### Default (empty dict)

When `--data` is omitted, an empty `{}` is submitted. This is valid for node types that accept
optional input.

## Node-Specific Input Shapes

Different INPUT node types expect different payload structures:

| Node Type | Expected payload |
|-----------|-----------------|
| `PLAIN_TXT_INPUT` | `{"text": "<string>"}` |
| `STRUCTURED_INPUT` | `{"<field>": "<value>", ...}` (schema defined by the workflow) |
| `FILE_UPLOAD` | `{"files": ["<path-or-url>", ...]}` |

Use `workflow status` to identify which node is currently waiting and what type it is.
The status command prints a hint with the node type when a node is paused.

## Pre-flight Validation

Before prompting for confirmation, the command verifies:

1. The workflow is not in a terminal state (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`).
2. The workflow is not paused at a review node (use `workflow review` instead).
3. The specified `--node-id` exists in the workflow.
4. The specified node is the one currently waiting for input.

If any check fails, the command exits with an error and a hint about the correct next action.

## Confirmation Prompt

After validation, the command displays a Y/N prompt before submitting:

```
Submit input to node <node-id>? [Y/n]:
```

Answer `n` to cancel without submitting.

## Output

On success:

```
Input submitted.
```

If the API acknowledges the submission but has not yet confirmed the workflow advanced:

```
Input submitted but not yet confirmed. Use workflow status to verify the workflow advanced.
```

With `--json`, the raw API response is printed and the command exits immediately.

## Examples

```bash
# Submit plain text input using .last_run context
workflow input --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
               --data '{"text": "Approve the invoice"}'

# Submit structured input from a file
workflow input --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
               --data @structured_payload.json

# Submit with explicit run context (bypass .last_run)
workflow input --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
               --data '{"text": "Hello"}' \
               --workflow-id 939843a8-6257-4475-bfc0-f7d6500d9f00 \
               --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Machine-readable response
workflow input --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
               --data '{"text": "Hello"}' \
               --json
```
