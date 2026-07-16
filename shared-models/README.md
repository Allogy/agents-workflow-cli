# agents-workflow-models

Shared Pydantic v2 schemas and enums for Agents Platform workflows.

This package provides the data contracts shared between the backend API and the workflow CLI, with **zero SQLAlchemy or SQLModel dependencies**.

## Installation

```bash
uv add agents-workflow-models
```

### Install from CodeArtifact (Private)

```bash
# Get credentials (from the repo root)
make codeartifact-login

# Add as a dependency
uv add agents-workflow-models \
  --index-url "https://aws:<TOKEN>@<ENDPOINT>/simple/"
```

See [`../docs/codeartifact.md`](../docs/codeartifact.md) for setup, publishing,
and CI/CD details.

## Usage

```python
from workflow_models import (
    NodeConfigType,
    ExecutionMode,
    WorkflowCreate,
    LogicalNodeCreate,
)

# Create a workflow
workflow = WorkflowCreate(
    version=1,
    state_schema={'input': 'string'},
    organization_id='...',
)

# Create a node
node = LogicalNodeCreate(
    workflow_id='...',
    workflow_version=1,
    config_type=NodeConfigType.AGENT,
    execution_mode=ExecutionMode.INPUT,
)
```

## Package Contents

### Enums
- `NodeConfigType` — 12 node configuration types
- `ExecutionMode` — INPUT, OUTPUT, MESSAGES, FLOW
- `EdgeType` — STATIC, CONDITIONAL, METADATA, RECURSIVE, MAPPING
- `StepExecutionType` — STEP, STREAM, JOIN, INPUT
- `ReducerType` — 7 reducer types for join nodes
- `PathType` — BEZIER, STRAIGHT, STEP
- `ExecutionStatus` — PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT
- `NodeExecutionStatus` — PENDING, RUNNING, COMPLETED, FAILED, SKIPPED

### Schemas (Create / Update / Public)
- Workflow, LogicalNode, LogicalNodeInput, LogicalNodeOutput
- LogicalEdge, WorkflowVisuals, NodeVisuals, EdgeVisuals
- WorkflowMetadata, WorkflowExecution

### WDF — Workflow Definition Format (`workflow_models.wdf`)

Pydantic v2 models for the **YAML-based workflow definition format**. These
represent the human-readable, file-based representation of workflows — distinct
from the API-oriented schemas above which use UUIDs and foreign keys.

```python
from workflow_models.wdf import (
    WorkflowDefinition,
    NodeDefinition,
    EdgeDefinition,
    extract_variable_refs,
)
```

**Top-level models:**

| Model | Description |
|-------|-------------|
| `WorkflowDefinition` | Root model — name, description, nodes (slug-keyed), edges, state schema |
| `NodeDefinition` | A single node — slug, type, config, execution_mode, label |
| `EdgeDefinition` | A directed edge — source/target slugs, type, optional condition |

**Node config schemas** (one per node type):

| Config class | Node type | Description |
|-------------|-----------|-------------|
| `PlainTxtInputConfig` | `PLAIN_TXT_INPUT` | Plain text input with optional placeholder |
| `StructuredInputConfig` | `STRUCTURED_INPUT` | JSON-schema-driven form input |
| `FileUploadConfig` | `FILE_UPLOAD` | File upload with accepted types and size limits |
| `AgentConfig` | `AGENT` | LLM agent with model, temperature, system prompt, optional `use_rlm` flag to route through the RLM (beta) runner, optional `web_tools_enabled` flag to attach `web_search` / `web_fetch` tools, optional `max_iterations` (int, 1-100, default 20) to cap RLM loop iterations |
| `RagAgentConfig` | `RAG_AGENT` | RAG-enhanced agent with retrieval parameters |
| `LlmCallConfig` | `LLM_CALL` | Direct LLM invocation (no agent loop) |
| `StructuredOutputConfig` | `STRUCTURED_OUTPUT` | Render structured data via template |
| `RetrieveConfig` | `RETRIEVE` | Vector store retrieval with top_k / threshold |
| `DocumentExtractionConfig` | `DOCUMENT_EXTRACTION` | Extract structured fields from documents |
| `HumanReviewConfig` | `HUMAN_REVIEW` | Human approval / rejection gate |

**Variable reference utilities:**

| Export | Description |
|--------|-------------|
| `VariableRef` | Parsed reference — `node_slug`, `output_name`, `field_path` |
| `extract_variable_refs(text)` | Extract all `{{slug.output.field}}` references from a string |

#### WDF Usage Example

```python
from workflow_models.wdf import (
    WorkflowDefinition,
    NodeDefinition,
    EdgeDefinition,
    AgentConfig,
    PlainTxtInputConfig,
    extract_variable_refs,
)

# Build a workflow programmatically
workflow = WorkflowDefinition(
    name='my-pipeline',
    description='A simple two-node pipeline',
    nodes={
        'user-input': NodeDefinition(
            type='plain_txt_input',
            label='User Input',
            execution_mode='INPUT',
            config=PlainTxtInputConfig(placeholder='Enter your question...'),
        ),
        'agent': NodeDefinition(
            type='agent',
            label='Assistant',
            execution_mode='MESSAGES',
            config=AgentConfig(
                primaryInput='{{user-input.output.text}}',
            ),
        ),
    },
    edges=[
        EdgeDefinition(from_node='user-input', to='agent'),
    ],
    entry='user-input',
    exit='agent',
)

# Extract variable references from a template string
refs = extract_variable_refs('Answer: {{agent.output.response}}')
# => [VariableRef(node_slug='agent', output_name='output', field_path='response')]
```

#### YAML Workflow Files

Example `.workflow.yaml` files are in the [`examples/`](examples/) directory:

- `invoice-processing.workflow.yaml` — realistic 4-node invoice pipeline
- `all-node-types.workflow.yaml` — reference file with all 9 CLI-supported node types
- `linear-pipeline.workflow.yaml` — 3-node pipeline (text input → LLM → structured output)
- `rag-workflow.workflow.yaml` — 4-node RAG pipeline (structured input → file upload → LLM → RAG agent)
- `agent-review.workflow.yaml` — 4-node agent pipeline with human review gate
- `retrieval-pipeline.workflow.yaml` — 5-node retrieval pipeline with document extraction

> **Note:** YAML serialization (load/dump) lives in the CLI layer
> (`cli.wdf_yaml`), not in shared-models. The shared-models package has
> **no PyYAML runtime dependency** — it is pure Pydantic.
