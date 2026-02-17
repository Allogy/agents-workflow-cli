# agents-workflow-models

Shared Pydantic v2 schemas and enums for Agents Platform workflows.

This package provides the data contracts shared between the backend API and the workflow CLI, with **zero SQLAlchemy or SQLModel dependencies**.

## Installation

```bash
uv add agents-workflow-models
```

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
