# Workflow Validate Command

## Overview

The `workflow validate` command performs offline validation of `.workflow.yaml` files with no API calls. It runs 10 comprehensive checks to catch errors early in the authoring process.

## Installation

```bash
cd workflow-cli
uv sync --all-groups
```

## Usage

```bash
# Basic usage
uv run workflow validate <path-to-workflow-file>

# Examples
uv run workflow validate my-workflow.workflow.yaml
uv run workflow validate shared-models/examples/linear-pipeline.workflow.yaml
```

## Exit Codes

- **0** = All checks passed (warnings are allowed)
- **1** = One or more checks failed

This makes it perfect for CI/CD pipelines:

```bash
# In your CI script
uv run workflow validate workflows/*.workflow.yaml || exit 1
```

## Validation Checks

The command runs 10 validation checks:

| # | Check | What It Does | Status Type |
|---|-------|--------------|-------------|
| 1 | **YAML Syntax** | Validates YAML is well-formed (with line numbers on error) | FAIL |
| 2 | **WDF Schema Conformance** | Validates against Pydantic models (structure, types, required fields) | FAIL |
| 3 | **Node Type Recognition** | Ensures all node types are valid (10 supported types) | FAIL |
| 4 | **Edge References** | Ensures edge `from`/`to` reference existing nodes | FAIL |
| 5 | **Entry/Exit Points** | Ensures entry/exit reference existing nodes | FAIL |
| 6 | **Graph Reachability** | Ensures all nodes are reachable from entry via DFS | WARN |
| 7 | **Cycle Detection** | Detects circular dependencies (DFS 3-color algorithm, excludes RECURSIVE edges) | FAIL |
| 8 | **Variable References** | Validates `{{slug.output.field}}` references point to existing nodes | FAIL |
| 9 | **Node Config Validation** | Validates node-type-specific config (e.g., LLM_CALL requires `model` and `template`) | FAIL |
| 10 | **Unsupported Node Types** | Detects use of node types not supported by the CLI (e.g., `document_extraction`) | FAIL |

### Status Types

- **✓ PASS** (green) — Check passed
- **⚠ WARN** (yellow) — Non-blocking issue detected (e.g., unreachable nodes)
- **✗ FAIL** (red) — Blocking error that prevents workflow execution

### Check 10: Unsupported Node Types

Verifies that no nodes use types that are not supported by the CLI.

Currently unsupported types:
- `document_extraction` — legacy node type not supported for CLI execution

**Status:** FAIL if any unsupported node types are found.

**Example error:**
```
FAIL  Unsupported Node Types  Unsupported node types found: extract (document_extraction)
```

## Output Format

Results are displayed in a Rich table with color-coded status:

```
Validating: my-workflow.workflow.yaml

┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                  ┃ Status ┃ Details                   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ YAML Syntax            │ ✓ PASS │                           │
│ WDF Schema Conformance │ ✓ PASS │                           │
│ Graph Reachability     │ ✓ PASS │                           │
│ Cycle Detection        │ ✗ FAIL │ Cycle detected: a -> b -> a │
│ Variable References    │ ✓ PASS │                           │
│ Node Type Recognition  │ ✓ PASS │                           │
│ Edge References        │ ✓ PASS │                           │
│ Entry/Exit Points      │ ✓ PASS │                           │
│ Node Config Validation │ ✓ PASS │                           │
│ Unsupported Node Types │ ✓ PASS │                           │
└────────────────────────┴────────┴───────────────────────────┘

Validation failed: 1 failures, 0 warnings, 9 passed
```

## Common Validation Errors

### 1. Invalid YAML Syntax

**Error:**
```
YAML parsing error at line 12, column 5: mapping values are not allowed here
```

**Fix:** Check for malformed YAML (missing colons, incorrect indentation, unclosed brackets)

### 2. Unknown Node Type

**Error:**
```
Schema validation failed:
nodes.my_node.type: Input should be 'plain_txt_input', 'structured_input', ...
```

**Fix:** Use one of the 10 supported node types (see `VALID_NODE_TYPES`)

### 3. Cycle Detected

**Error:**
```
Cycle detected: node_a -> node_b -> node_c -> node_a
```

**Fix:** Remove the circular dependency or use a `RECURSIVE` edge type if the loop is intentional

### 4. Unreachable Nodes

**Warning:**
```
Unreachable nodes from entry point: orphan_node
```

**Fix:** Either connect the node to the graph or remove it

### 5. Invalid Variable Reference

**Error:**
```
Invalid variable references to non-existent nodes: nonexistent_slug
```

**Fix:** Ensure `{{slug.output.field}}` references use valid node slugs defined in `nodes`

### 6. Missing Required Config Field

**Error:**
```
Schema validation failed:
nodes.llm.config.template: Field required
```

**Fix:** Add the required field to the node's config block

### 7. Unsupported Node Type

**Error:**
```
FAIL  Unsupported Node Types  Unsupported node types found: extract (document_extraction)
```

**Fix:** Replace the unsupported node type with a supported alternative, or remove the node.

Currently unsupported types:
- `document_extraction` — legacy node type not supported for CLI execution

## Examples

### Example 1: Valid Workflow

```yaml
name: Simple Workflow
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config:
      placeholder: Enter text
  process:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: anthropic.claude-3-5-sonnet-20241022-v2:0
      template: "Process: {{input.output.text}}"
  output:
    type: structured_output
    execution_mode: OUTPUT
    config: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
```

**Result:** All 10 checks pass ✓

### Example 2: Workflow with Cycle

```yaml
name: Cycle Example
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  b:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
edges:
  - from: a
    to: b
  - from: b
    to: a  # Creates a cycle!
entry: a
exit: b
```

**Result:** Cycle Detection fails ✗

### Example 3: Allowed Recursive Loop

```yaml
name: Recursive Workflow
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  b:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
  c:
    type: structured_output
    execution_mode: OUTPUT
    config: {}
edges:
  - from: a
    to: b
  - from: b
    to: c
  - from: b
    to: a
    type: RECURSIVE  # Allowed recursive edge
entry: a
exit: c
```

**Result:** All 10 checks pass ✓ (recursive edges are excluded from cycle detection)

## Integration with CI/CD

### GitHub Actions

```yaml
- name: Validate Workflows
  run: |
    cd workflow-cli
    uv sync --all-groups
    for file in workflows/*.workflow.yaml; do
      uv run workflow validate "$file" || exit 1
    done
```

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

for file in $(git diff --cached --name-only --diff-filter=ACM | grep '\.workflow\.yaml$'); do
  uv run workflow validate "$file" || exit 1
done
```

## Tips

1. **Validate early, validate often** — Run validation as you author workflows, not just before deployment
2. **Warnings are informative** — Unreachable nodes won't block validation but indicate potential issues
3. **Use example files** — Start from `shared-models/examples/*.workflow.yaml` for reference
4. **Check variable references** — The validator catches typos in `{{slug.output.field}}` patterns
5. **Understand recursive edges** — Use `type: RECURSIVE` for intentional loops (e.g., retry logic)

## Troubleshooting

### Command not found

Make sure you've installed the CLI:

```bash
cd workflow-cli
uv sync --all-groups
```

### Import errors

If you see module import errors, ensure shared-models is installed:

```bash
cd workflow-cli/shared-models
uv sync --all-groups
cd ..
uv sync --all-groups
```

### File not found

Use absolute paths or paths relative to your current directory:

```bash
# Absolute path
uv run workflow validate /home/user/workflows/my-workflow.yaml

# Relative path
cd workflows
uv run workflow validate my-workflow.yaml
```

## Related Documentation

- [Workflow Definition Format (WDF) Spec](../shared-models/README.md)
- [Node Type Reference](../shared-models/src/workflow_models/wdf/nodes.py)
- [Example Workflows](../shared-models/examples/)
- [API Client Usage](./README.md#api-client)

## Implementation Details

- **No API calls** — All validation is offline and operates on local files
- **Pure functions** — Graph validation logic is reusable (also used in backend)
- **Fast** — Validates in milliseconds even for large workflows
- **Deterministic** — Same input always produces same output
- **Exit code friendly** — Use in scripts and CI pipelines

---

**Reference:** Jira ticket RAG-947
