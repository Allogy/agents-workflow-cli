# Temporal Run Command - Test Playbook

Step-by-step guide to reproduce the full test suite for `workflow run` temporal execution across all 7 templates and all execution modes.

> **Status as of 2026-03-05:** BUG-1, BUG-2, BUG-3 are resolved. BUG-5 (multi-node workflows stuck at input) and CLI-1 (status cross-workflow run-id lookup) are tracked in `docs/plans/2026-03-05-fix-bug5-cli1.md`.

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

**Expected:** Prints run ID and exits 0. Note: `--input` is not auto-submitted in `--no-follow` mode — a hint message is printed. If Temporal is down, expect exit 1 with "Temporal service unavailable".

**Record:** Workflow ID, Run ID, exit code, any error message.

### Step 2.2: Streaming mode

For each template:
```bash
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
workflow run <workflow-id> --input '<json>' --stream --verbose
```

**Expected:** SSE events showing `RUN_STARTED`, `WAITING_FOR_INPUT` with node type and payload, then "Auto-submitted input to <node-id>". Stream closes after auto-submit.

**Known behavior:** After auto-submit the stream closes with "⏸ Workflow paused". This is expected — the stream doesn't hold open to wait for post-submit events. Use polling mode (Step 2.3) for end-to-end completion.

### Step 2.3: Polling mode (default)

```bash
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
timeout 60 workflow run <workflow-id> --input '<json>'
```

**Expected (single-node workflows such as blank):** Auto-submits input and shows "✓ Workflow completed".

**Expected (multi-node workflows):** Auto-submits to first input node, then shows "⏸ Workflow paused — waiting for input" at the same node again.

> **Known issue (BUG-5):** Multi-node workflows do not advance past the input node after signal submission. Single-node (blank) works. Fix tracked in `docs/plans/2026-03-05-fix-bug5-cli1.md`.

---

## Phase 3: Test Status Command

### Step 3.1: Status via .last_run

After running any workflow:
```bash
workflow status
```

**Expected:** Rich table with Node/Type/Status columns. Verify:
- [ ] Overall status line shows correct `execution_status`
- [ ] Node statuses show `WAITING_FOR_INPUT` for paused node
- [ ] Hints appear for paused nodes (e.g., "Tip: workflow input ...")

### Step 3.2: Status with explicit run-id (CLI-1 check)

```bash
workflow status <run-id-from-a-different-workflow>
```

**Expected (current behavior):** Returns "not found" because it combines the foreign run-id with the workflow-id from `.last_run`.

**Expected (after CLI-1 fix):** Returns a clear error: "Run ID not found in last-run context. Use --workflow-id to specify the workflow."

> **Known issue (CLI-1):** `workflow status <run-id>` silently uses `workflow_id` from `.last_run` even when the run-id belongs to a different workflow. Fix tracked in `docs/plans/2026-03-05-fix-bug5-cli1.md`.

### Step 3.3: JSON output mode

```bash
workflow status --json
```

**Expected:** Raw JSON with the following fields all populated correctly:
- [ ] `execution_status` present and accurate
- [ ] `waiting_for_input_node_id` present when paused at INPUT ✅ (was BUG-1, now fixed)
- [ ] `review_node_id` present when paused at REVIEW ✅ (was BUG-2, now fixed)
- [ ] `current_node_id` matches `current_node` top-level
- [ ] Top-level `status` matches `state.execution_status` ✅ (was BUG-3, now fixed)

### Step 3.4: Status via raw API (compare)

```bash
curl -s -H "X-API-Key: <key>" \
  "https://<host>/v2/workflows/<wf-id>/status?run_id=<run-id>" | python3 -m json.tool
```

Compare raw API response with CLI `--json` output to identify any field mapping issues.

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
- [ ] Pre-flight validation detects `WAITING_FOR_INPUT` state
- [ ] Confirmation prompt appears
- [ ] Input submitted (HTTP 200 with "submitted and confirmed")

### Step 4.3: Direct API input submission

```bash
curl -s -w "\nHTTP: %{http_code}\n" -X POST \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"node_id": "<node-id>", "input_data": {"text": "test"}}' \
  "https://<host>/v2/workflows/<wf-id>/input?run_id=<run-id>"
```

**Expected:** `200 {"status": "submitted", ...}`.

Then check status after 30 seconds:

```bash
curl -s -H "X-API-Key: <key>" \
  "https://<host>/v2/workflows/<wf-id>/status?run_id=<run-id>" | python3 -m json.tool
```

**Verify (single-node blank):**
- [ ] `execution_status` is `COMPLETED`
- [ ] `execution_history` includes the INPUT node ID

**Verify (multi-node, after BUG-5 fix):**
- [ ] `current_node_id` moved from the INPUT node to the next node
- [ ] `execution_history` includes the INPUT node ID

> **Known issue (BUG-5):** Multi-node workflows stay at `WAITING_FOR_INPUT` with empty `execution_history` after submission. Fix tracked in `docs/plans/2026-03-05-fix-bug5-cli1.md`.

### Step 4.4: --input auto-submission check

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

**Expected (single-node blank after polling mode):** Workflow completes.
**Expected (`--no-follow`):** Input is NOT auto-submitted. The hint message is shown: "use `workflow input <run-id> --data '{...}'`".

---

## Phase 5: Test Human Review Flow

This phase requires a workflow to reach the HUMAN_REVIEW node. Use **Form with Review** template.

> **Prerequisite:** BUG-5 must be fixed for this phase to work end-to-end, since the form-with-review workflow must advance past the input node to reach the LLM and then the HUMAN_REVIEW node.

### Step 5.1: Advance to review node

1. Start workflow: `workflow run <form-review-id> --no-follow`
2. Submit input (via curl or CLI `workflow input`):
   ```bash
   curl -s -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d '{"node_id": "<input-node-id>", "input_data": {"title": "Test", "description": "Review test", "priority": "high"}}' \
     "https://<host>/v2/workflows/<wf-id>/input?run_id=<run-id>"
   ```
3. Wait for LLM_CALL to complete (30-60s)
4. Check status — should show `WAITING_FOR_REVIEW` with `review_node_id` populated ✅

### Step 5.2: CLI review command

```bash
echo "y" | workflow review \
  --run-id <run-id> \
  --node-id <review-node-id> \
  --approve
```

**Check:**
- [ ] Pre-flight finds `review_node_id` in state ✅ (was BUG-2, now fixed)
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
# Expected: "Error: --comment is required with --reject and --revise." (exit 2)

# Multiple decision flags
workflow review --run-id <id> --node-id <id> --approve --reject
# Expected: "Error: Specify exactly one of --approve, --reject, or --revise." (exit 2)

# Wrong node type
workflow review --run-id <id> --node-id <input-node-id> --approve
# Expected: "Error: Node ... is type STRUCTURED_INPUT, not HUMAN_REVIEW." (exit 2)
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

### Step 7.1: Blank workflow (single node) ✅ works today

```bash
timeout 60 workflow run <blank-id> --input '{"text": "E2E test"}'
```

**Expected:** `waiting_for_input` → auto-submit → "✓ Workflow completed" (exit 0).

### Step 7.2–7.4: Multi-node E2E (requires BUG-5 fix)

After BUG-5 is resolved, re-test these:

```bash
# Text to Agent (input -> agent)
timeout 120 workflow run <text-agent-id> --input '{"text": "What is AI?"}'
# Expected: input auto-submitted → agent runs → COMPLETED

# Batch Processing (input -> llm -> llm)
timeout 180 workflow run <batch-id> --input '{"text": "Apple\nBanana\nCherry"}'
# Expected: input → LLM1 → LLM2 → COMPLETED

# Form with Review (input -> llm -> review)
workflow run <review-id> --input '{"title": "Test", "description": "Desc", "priority": "high"}' --stream -i
# Expected: input → LLM assessment → WAITING_FOR_REVIEW → approve interactively → COMPLETED
```

---

## Checklist Summary

### Infrastructure Health

- [ ] Backend API responsive (`workflow list` succeeds)
- [ ] Temporal server running (workflows start)
- [ ] Temporal worker running and connected to task queue
- [ ] Redis running (SSE streaming delivers events)

### Bug Regression Checks

| Bug | Description | Status |
|-----|-------------|--------|
| BUG-1 | `state.waiting_input_node_id` present in status response | ✅ Fixed |
| BUG-2 | `state.review_node_id` present + pre-flight check works | ✅ Fixed |
| BUG-3 | Top-level `status` reflects HITL states | ✅ Fixed |
| BUG-5 | Input signal advances multi-node workflows | ❌ Open — see plan |
| CLI-1 | `workflow status <run-id>` works across different workflow contexts | ❌ Open — see plan |

### Feature Coverage

- [ ] `--no-follow` mode starts and returns run ID
- [ ] `--stream` mode shows real-time SSE events (`RUN_STARTED`, `WAITING_FOR_INPUT`)
- [ ] `--stream --verbose` shows detailed payloads
- [ ] `--stream -i` interactive mode prompts for input/review
- [ ] Default polling mode auto-submits and completes (single-node)
- [ ] Default polling mode detects HITL gates and prints hint
- [ ] `workflow input` finds `.last_run` context and submits data
- [ ] `workflow review --approve` submits approval
- [ ] `workflow review --reject --comment` submits rejection
- [ ] `workflow review --revise --comment` submits revision request
- [ ] `workflow status` shows accurate per-node statuses
- [ ] `workflow status --json` returns raw API response
- [ ] Name resolution works (UUID, lockfile, API name search)
- [ ] Error messages are clear for invalid inputs
- [ ] Idempotent push (update mode)
