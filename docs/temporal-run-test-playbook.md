# Temporal Run Command - Test Playbook

Step-by-step guide to reproduce the full test suite for `workflow run` temporal execution across all 7 templates and all execution modes.

---

## Prerequisites

### Environment Setup

1. **Install CLI dependencies:**
   ```bash
   cd workflow-cli/
   uv sync --all-groups
   ```

2. **Configure credentials** (`~/.workflow/config.yaml`):
   ```yaml
   host: https://dev.sb.allogy.com
   api_key: <your-api-key>
   org_id: <your-org-id>
   ```

3. **Create a clean test directory:**
   ```bash
   mkdir -p /tmp/workflow-test && cd /tmp/workflow-test
   rm -f *.yaml *.lock .workflow.last_run
   ```

4. **Verify connectivity:**
   ```bash
   workflow list
   ```
   Expected: Table of existing workflows. If this fails, check credentials and network.

---

## Phase 1: Push All Templates

Copy templates from the CLI source and give them unique "Test" prefixed names to avoid collisions.

### Step 1.1: Copy and rename templates

```bash
TEMPLATES_DIR="<repo>/workflow-cli/src/cli/templates"

for template in blank text-to-agent batch-processing simple-form \
                form-with-review rag-qa document-analysis; do
  cp "${TEMPLATES_DIR}/${template}.workflow.yaml" "test-${template}.workflow.yaml"
  sed -i "s/^name: /name: Test /" "test-${template}.workflow.yaml"
done
```

### Step 1.2: Validate all templates

```bash
for yaml in test-*.workflow.yaml; do
  echo -n "$yaml: "
  workflow validate "$yaml" 2>&1 | tail -1
done
```

**Expected:** All 7 files show "All 10 checks passed".

### Step 1.3: Push all templates

```bash
for yaml in test-*.workflow.yaml; do
  echo "--- Pushing: $yaml ---"
  workflow push "$yaml"
  echo ""
done
```

**Expected:** Each shows "Created workflow: <uuid>" with exit code 0.

### Step 1.4: Record workflow IDs

```bash
workflow list
```

Note the IDs for each "Test ..." workflow. You'll need these for the run tests.

### Step 1.5: Test idempotent re-push

```bash
workflow push test-blank.workflow.yaml
```

**Expected:** Shows "Update mode" and "Updated workflow: <same-uuid>".

---

## Phase 2: Test Execution Modes

For each test, record the timestamp, output, and exit code.

### Test input data per template

| Template | `--input` value |
|----------|----------------|
| Blank | `'{"text": "Hello from CLI test"}'` |
| Text to Agent | `'{"text": "What is artificial intelligence?"}'` |
| Batch Processing | `'{"text": "Apple\nBanana\nCherry"}'` |
| Simple Form | `'{"request": "Summarize ML basics", "context": "For beginners"}'` |
| Form with Review | `'{"title": "Test Request", "description": "Test description", "priority": "medium"}'` |
| RAG Q&A | `'{"text": "What is retrieval augmented generation?"}'` |
| Document Analysis | `'{"text": "Analyze this test document"}'` |

### Step 2.1: No-follow mode (fire-and-forget)

For each template:
```bash
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
workflow run <workflow-id> --input '<json>' --no-follow
```

**Expected:** Should print run ID and exit 0. If Temporal is down, expect exit 1 with "Temporal service unavailable".

**Record:** Workflow ID, Run ID, exit code, any error message.

### Step 2.2: Streaming mode

For each template:
```bash
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
workflow run <workflow-id> --input '<json>' --stream --verbose
```

**Expected (healthy):** SSE events showing RUN_STARTED, STEP_STARTED, WAITING_FOR_INPUT, etc.

**Known failure:** If Redis is down, expect: "Redis service unavailable: Error 111 connecting to localhost:6379".

### Step 2.3: Polling mode (default)

```bash
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
timeout 60 workflow run <workflow-id> --input '<json>'
```

**Expected (healthy):** Should show node progress and eventually reach WAITING_FOR_INPUT or COMPLETED.

**Known issue:** If top-level `status` stays "RUNNING" while `execution_status` is "WAITING_FOR_INPUT", the poll loop never exits (BUG-3). Use `timeout` to cap at 60s.

---

## Phase 3: Test Status Command

### Step 3.1: Status via .last_run

After running any workflow with `--no-follow`:
```bash
workflow status
```

**Expected:** Rich table with Node/Type/Status columns. Verify:
- [ ] Overall status line shows correct execution_status
- [ ] Node statuses accurately reflect WAITING_FOR_INPUT vs PENDING
- [ ] Hints appear for paused nodes (e.g., "Tip: workflow input ...")

### Step 3.2: Status with explicit run-id

```bash
workflow status <run-id>
```

**Known issue (CLI-1):** Uses `workflow_id` from `.last_run`. If `.last_run` is from a different workflow, this returns "not found". Verify this behavior.

### Step 3.3: JSON output mode

```bash
workflow status --json
```

**Expected:** Raw JSON with `workflow_id`, `run_id`, `status`, `current_node`, and `state` object.

**Verify state fields:**
- [ ] `execution_status` present and accurate
- [ ] `waiting_input_node_id` present when paused at INPUT (BUG-1 check)
- [ ] `review_node_id` present when paused at REVIEW (BUG-2 check)
- [ ] `current_node_id` matches `current_node` top level
- [ ] Top-level `status` matches `state.execution_status` (BUG-3 check)

### Step 3.4: Status via raw API (compare)

```bash
curl -s -H "X-API-Key: <key>" \
  "https://<host>/v2/workflows/<wf-id>/status?run_id=<run-id>" | python3 -m json.tool
```

Compare raw API response with CLI output to identify any field mapping issues.

---

## Phase 4: Test Input Submission

### Step 4.1: Get node IDs

```bash
curl -s -H "X-API-Key: <key>" \
  "https://<host>/v1/workflow-nodes/?workflow_id=<wf-id>" | python3 -c "
import sys, json
for n in json.load(sys.stdin):
    print(f\"  {n['id']}  type={n.get('config_type','?')}\")
"
```

### Step 4.2: CLI input command

```bash
echo "y" | workflow input --node-id <input-node-id> --data '{"text": "test"}'
```

**Check:**
- [ ] Command finds the correct `.last_run` context
- [ ] Pre-flight validation detects WAITING_FOR_INPUT state (BUG-1 check)
- [ ] If validation passes, input is submitted and workflow advances

### Step 4.3: Direct API input submission (bypass CLI validation)

```bash
curl -s -w "\nHTTP: %{http_code}\n" -X POST \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"node_id": "<node-id>", "input_data": {"text": "test"}}' \
  "https://<host>/v2/workflows/<wf-id>/input?run_id=<run-id>"
```

**Expected:** `200 {"status": "submitted"}`. Then check status after 15-30 seconds:

```bash
curl -s -H "X-API-Key: <key>" \
  "https://<host>/v2/workflows/<wf-id>/status?run_id=<run-id>" | python3 -m json.tool
```

**Verify:**
- [ ] Workflow advanced to next node (BUG-5 check)
- [ ] `current_node_id` changed from the INPUT node to the next node
- [ ] `execution_history` includes the INPUT node ID

### Step 4.4: Test --input auto-submission (BUG-4 check)

Start a workflow with `--input` and immediately check if it auto-advances past the INPUT node:

```bash
workflow run <wf-id> --input '{"text": "test"}' --no-follow
sleep 5
workflow status --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d['state']
print(f\"exec_status: {s['execution_status']}\")
print(f\"current_node: {s.get('current_node_id')}\")
print(f\"history: {s.get('execution_history')}\")
"
```

**Expected (if BUG-4 fixed):** Workflow should be past the INPUT node.
**Current behavior:** Workflow pauses at INPUT node requiring separate submission.

---

## Phase 5: Test Human Review Flow

This requires a workflow to reach the HUMAN_REVIEW node. Use **Form with Review** template.

### Step 5.1: Advance to review node

1. Start workflow: `workflow run <form-review-id> --no-follow`
2. Submit input (via curl if CLI blocked by BUG-1):
   ```bash
   curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d '{"node_id": "<input-node-id>", "input_data": {"title": "Test", "description": "Review test", "priority": "high"}}' \
     "https://<host>/v2/workflows/<wf-id>/input?run_id=<run-id>"
   ```
3. Wait for LLM_CALL to complete (30-60s)
4. Check status - should show `WAITING_FOR_REVIEW`

### Step 5.2: CLI review command

```bash
echo "y" | workflow review \
  --run-id <run-id> \
  --node-id <review-node-id> \
  --approve
```

**Check:**
- [ ] Pre-flight finds `review_node_id` in state (BUG-2 check)
- [ ] Confirmation prompt appears
- [ ] Review submitted successfully

### Step 5.3: Test reject with comment

```bash
echo "y" | workflow review \
  --run-id <run-id> \
  --node-id <review-node-id> \
  --reject --comment "Not ready"
```

### Step 5.4: Test revise with comment

```bash
echo "y" | workflow review \
  --run-id <run-id> \
  --node-id <review-node-id> \
  --revise --comment "Please update section 3"
```

### Step 5.5: Validation checks

```bash
# Missing --comment with --reject
workflow review --run-id <id> --node-id <id> --reject
# Expected: "Error: --comment is required with --reject and --revise."

# Multiple decision flags
workflow review --run-id <id> --node-id <id> --approve --reject
# Expected: "Error: Specify exactly one of --approve, --reject, or --revise."

# Wrong node type
workflow review --run-id <id> --node-id <input-node-id> --approve
# Expected: "Error: Node ... is type STRUCTURED_INPUT, not HUMAN_REVIEW."
```

---

## Phase 6: Test Error Handling

### Step 6.1: Non-existent workflow name

```bash
workflow run "Nonexistent Workflow" --no-follow
```

**Expected:** Exit 2 with "Did you mean:" suggestions.

### Step 6.2: Non-existent UUID

```bash
workflow run "00000000-0000-0000-0000-000000000000" --no-follow
```

**Expected:** Exit 1 with "Workflow ... not found" (HTTP 404).

### Step 6.3: Invalid JSON input

```bash
workflow run <wf-id> --input 'not json' --no-follow
```

**Expected:** Exit 2 with "Invalid JSON input" error and usage hint.

### Step 6.4: Invalid YAML validation

```bash
echo -e "name: Bad\nnodes: {}" > bad.workflow.yaml
workflow validate bad.workflow.yaml
```

**Expected:** Exit 1 showing schema failures (missing edges, entry, exit).

### Step 6.5: Name resolution by lockfile

```bash
# In directory with .workflow.lock files
workflow run "Test Blank Workflow" --no-follow
```

**Expected:** Resolves via lockfile, shows correct workflow ID.

---

## Phase 7: End-to-End Verification

This phase verifies a complete workflow lifecycle. Requires all infrastructure issues resolved.

### Step 7.1: Blank workflow (single node)

```bash
workflow run <blank-id> --input '{"text": "E2E test"}' --stream --verbose
```

**Expected:** RUN_STARTED -> WAITING_FOR_INPUT -> (submit) -> RUN_FINISHED

### Step 7.2: Text to Agent (input -> agent)

```bash
workflow run <text-agent-id> --input '{"text": "What is AI?"}' --stream -i
```

**Expected:** Pauses at input -> submit interactively -> agent processes -> COMPLETED

### Step 7.3: Batch Processing (input -> llm -> llm)

```bash
workflow run <batch-id> --input '{"text": "Apple\nBanana"}' --stream --verbose
```

**Expected:** Input -> LLM processes items -> LLM summarizes -> COMPLETED

### Step 7.4: Form with Review (input -> llm -> review)

```bash
workflow run <review-id> --input '{"title": "Test", "description": "Desc", "priority": "high"}' --stream -i
```

**Expected:** Input -> LLM assessment -> WAITING_FOR_REVIEW -> approve interactively -> COMPLETED

---

## Checklist Summary

### Infrastructure Health

- [ ] Redis running (`redis-cli ping` or check port 6379)
- [ ] Temporal server running (check port 7233)
- [ ] Temporal worker running and connected to task queue
- [ ] Backend API responsive (`workflow list` succeeds)

### Bug Regression Checks

- [ ] BUG-1: `state.waiting_input_node_id` present in status response
- [ ] BUG-2: `state.review_node_id` present in status response
- [ ] BUG-3: Top-level `status` reflects HITL states (not stuck on "RUNNING")
- [ ] BUG-4: `--input` auto-submits to first INPUT node
- [ ] BUG-5: Input signal actually advances multi-node workflows
- [ ] CLI-1: `workflow status <run-id>` works across different workflow contexts

### Feature Coverage

- [ ] `--no-follow` mode starts and returns run ID
- [ ] `--stream` mode shows real-time SSE events
- [ ] `--stream --verbose` shows detailed payloads
- [ ] `--stream -i` interactive mode prompts for input/review
- [ ] Default polling mode detects HITL gates and terminal states
- [ ] `workflow input` submits data to paused INPUT nodes
- [ ] `workflow review --approve` submits approval
- [ ] `workflow review --reject --comment` submits rejection
- [ ] `workflow review --revise --comment` submits revision request
- [ ] `workflow status` shows accurate per-node statuses
- [ ] `workflow status --json` returns raw API response
- [ ] Name resolution works (UUID, lockfile, API name search)
- [ ] Error messages are clear for invalid inputs
