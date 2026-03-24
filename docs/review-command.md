# `workflow review` Command

Submit a human review decision (approve, reject, or revise) to a paused `HUMAN_REVIEW` node
in a running workflow execution.

## Usage

```
workflow review --run-id UUID --node-id UUID (--approve | --reject | --revise) [--comment TEXT] [--workflow-id UUID] [--json]
```

## Options

| Option | Description |
|--------|-------------|
| `--run-id` | **(Required)** Run ID of the execution to review |
| `--node-id` | **(Required)** ID of the `HUMAN_REVIEW` node to validate against |
| `--approve` | Submit an approval decision |
| `--reject` | Submit a rejection decision. Requires `--comment` |
| `--revise` | Request a revision. Requires `--comment` |
| `--comment` | Feedback text. Required with `--reject` and `--revise`; optional with `--approve` |
| `--workflow-id` | Explicit workflow ID. When omitted, resolved from `.last_run` using `--run-id` |
| `--json` | Print raw JSON response and exit |

## Decision Rules

Exactly one of `--approve`, `--reject`, or `--revise` must be provided. Specifying more than one,
or none at all, is an error.

`--comment` is **required** with `--reject` and `--revise`. It is optional with `--approve`.

| Decision | `--comment` | Success message |
|----------|-------------|-----------------|
| `--approve` | Optional | `Approved.` |
| `--reject` | Required | `Rejected.` |
| `--revise` | Required | `Revision requested.` |

## Run Context Resolution

`--run-id` is required for the review command (unlike `workflow status` and `workflow input`,
there is no auto-read from `.last_run` for the run ID itself). Context resolution works as follows:

1. **`--run-id` only** — the command reads `workflow_id` from `.last_run` by matching the provided run ID
2. **`--run-id` + `--workflow-id`** — bypasses `.last_run`; both values are used directly

## Pre-flight Validation

Before prompting for confirmation, the command verifies:

1. The workflow is not in a terminal state (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`).
2. The workflow is not paused at an input node instead (use `workflow input` in that case).
3. The specified `--node-id` exists in the workflow.
4. The specified node has type `HUMAN_REVIEW`.
5. The specified node is the one currently paused for review.

If any check fails, the command exits with an error and a hint about the correct next action.

Note: the `--node-id` flag is used only for pre-flight validation. The API automatically targets
the currently paused review node and does not require the node ID in the request body.

## Confirmation Prompt

After validation, the command displays a Y/N prompt before submitting:

```
Submit review: approve? [Y/n]:
```

Answer `n` to cancel without submitting.

## Output

On success, one of:

```
Approved.
Rejected.
Revision requested.
```

With `--json`, the raw API response is printed and the command exits immediately.

## Examples

```bash
# Approve using .last_run context for workflow_id
workflow review --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
                --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
                --approve

# Reject with required comment
workflow review --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
                --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
                --reject \
                --comment "Invoice total does not match the purchase order"

# Request revision with required comment
workflow review --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
                --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
                --revise \
                --comment "Please recheck section 3 and resubmit"

# Explicit workflow ID (bypass .last_run)
workflow review --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
                --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
                --workflow-id 939843a8-6257-4475-bfc0-f7d6500d9f00 \
                --approve

# Machine-readable response
workflow review --run-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
                --node-id c3d4e5f6-a7b8-9012-cdef-345678901234 \
                --approve \
                --json
```
