# WDF Syntax Reference

WDF (Workflow Definition Format) is the YAML-based format for authoring workflows on the Capillary Actions platform. Files use the `.workflow.yaml` extension.

## Top-Level Structure

Every WDF file has these top-level fields:

```yaml
name: Invoice Processing Pipeline          # Required. Display name.
description: Extract data from invoices     # Optional. Describes the workflow.
version: 1                                  # Required. Schema version (always 1).
tags:                                       # Optional. List of string tags.
  - finance
  - document-processing

nodes:                                      # Required. Map of slug -> node definition.
  upload:
    type: file_upload
    execution_mode: INPUT
    label: Upload Invoice
    config:
      acceptedFormats: [pdf, png, jpg]
      maxFileSize: 10485760

edges:                                      # Required. List of connections between nodes.
  - from: upload
    to: extract

entry: upload                               # Required. Slug of the first node.
exit: extract                               # Required. Slug of the last node.
```

## Node Definition Fields

Each node under `nodes:` has this shape:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Node type string (see Node Types Reference) |
| `execution_mode` | Yes | One of: `INPUT`, `OUTPUT`, `MESSAGES`, `FLOW` |
| `label` | No | Human-readable display name |
| `config` | Yes | Type-specific configuration (see each node type) |
| `timeout_seconds` | No | Override the default execution timeout |

## Node Slugs

Slugs are the keys under `nodes:`. They are used in edges, entry/exit, and variable references.

Rules:
- Use `snake_case` (e.g., `upload_invoice`, `extract_fields`)
- Must be unique within the workflow
- Referenced in variable templates as `{{slug.output.field}}`

## Edge Definition Fields

Each edge connects two nodes:

```yaml
edges:
  - from: source_slug       # Required. Node slug where the edge starts.
    to: target_slug          # Required. Node slug where the edge ends.
    type: STATIC             # Optional. Default is STATIC for linear flows.
    condition: route == "x"  # Only for CONDITIONAL edges.
```

### Edge Types

| Type | When to Use |
|------|-------------|
| `STATIC` | Default. Unconditional flow from one node to the next. Used in linear pipelines. |
| `CONDITIONAL` | Branch based on a condition string. Multiple CONDITIONAL edges from one node create decision points. |
| `RECURSIVE` | Loop back to a previous node. Used for retry/iteration patterns. |

For generated workflows, always use `STATIC` edges in a linear chain.

## Entry and Exit

```yaml
entry: first_node_slug    # Execution starts here
exit: last_node_slug      # Execution ends here
```

Both must reference existing node slugs. The entry node is typically an input node. The exit node is the last processing or review node.

## Variable References

The `{{slug.output.field}}` syntax lets nodes reference outputs from upstream nodes.

```yaml
template: "Question: {{user_input.output.text}}"
```

Rules:
- The slug must be a node that runs before this node (upstream in the edge graph)
- The path must include `.output.` followed by a specific field name
- Using just `{{slug.output}}` without a field will fail at runtime

See the Variable References document for the valid output paths per node type.

## Execution Modes

| Mode | Used By | Description |
|------|---------|-------------|
| `INPUT` | plain_txt_input, structured_input, file_upload | Pauses workflow for user input |
| `MESSAGES` | llm_call, agent, rag_agent | LLM/agent processing with message history |
| `OUTPUT` | structured_output | Produces validated structured data |
| `FLOW` | retrieve, human_review | Control flow or data retrieval step |

## Validation

Run `workflow validate <file.yaml>` to check a WDF file. It runs 13 checks:

1. YAML syntax parsing
2. WDF schema conformance (Pydantic validation)
3. Node type recognition (all types are known)
4. Edge references (from/to reference existing nodes)
5. Entry/exit points (reference existing nodes)
6. Graph reachability (all nodes reachable from entry)
7. Cycle detection (no unintended loops)
8. Variable reference validation (slugs exist, are upstream)
9. Node config validation (per-type field validation)
10. Unsupported node types (e.g., document_extraction)
11. Output variable paths (field paths match registry)
12. Inactive node types (registry status check)
13. Field coverage drift (WDF vs registry schema comparison)
