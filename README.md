# Agents Workflow CLI

CLI tool for managing and executing workflows on the Agents Platform.

## Prerequisites

- **Python** >= 3.13
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

## Quick Start

```bash
# Install dependencies
uv sync --all-groups

# Run the CLI
uv run workflow --help

# Or run as a Python module
uv run python -m cli --help
```

## Install from CodeArtifact (Private)

The CLI and shared models are published to a private AWS CodeArtifact registry.

**First-time setup** (creates the domain and repository):

```bash
# From the repo root
make codeartifact-setup
```

**Install the CLI**:

```bash
# Get credentials (prints token + endpoint)
make codeartifact-login

# Then install
uv tool install agents-workflow-cli \
  --index-url "https://aws:<TOKEN>@<ENDPOINT>/simple/"
```

**Publish a new version**:

```bash
make codeartifact-publish-cli
make codeartifact-publish-models
```

See [`docs/codeartifact.md`](docs/codeartifact.md) for full details on defaults,
CI/CD, and release validation.

## Development

```bash
# Install all dependencies (including dev tools)
uv sync --all-groups

# Run linter
uv run ruff check .

# Run formatter
uv run ruff format .

# Auto-fix lint issues
uv run ruff check . --fix

# Run tests
uv run pytest

# Install pre-commit hooks
uv run pre-commit install
```

## Project Structure

```
workflow-cli/
  pyproject.toml              # Package configuration (uv-managed)
  src/
    cli/
      __init__.py             # Package init with version
      __main__.py             # Enables `python -m cli`
      main.py                 # Typer app entry point
      client.py               # WorkflowClient — httpx-based API client
      config.py               # CLIConfig — host/api_key/org_id resolution
      exceptions.py           # Typed API exception hierarchy
      wdf_yaml.py             # WDF YAML load/dump helpers (PyYAML wrapper)
      lockfile.py             # Lockfile management for idempotent push operations
      commands/
        push.py               # Push command — deploy workflows to platform
  shared-models/              # agents-workflow-models (published separately)
    pyproject.toml
    src/workflow_models/
      __init__.py             # Re-exports enums, schemas, and WDF models
      enums.py                # NodeConfigType, EdgeType, ExecutionMode, etc.
      schemas/                # API-oriented schemas (Create/Update/Public)
      wdf/                    # WDF models — YAML-oriented workflow definitions
        __init__.py           #   Re-exports all WDF types
        nodes.py              #   NodeDefinition + 10 node config schemas
        edges.py              #   EdgeDefinition (source/target slug-based)
        workflow.py           #   WorkflowDefinition (root model with validation)
        variable_ref.py       #   VariableRef + extract_variable_refs()
    examples/                 # Example .workflow.yaml files
      invoice-processing.workflow.yaml
      all-node-types.workflow.yaml
      linear-pipeline.workflow.yaml
      rag-workflow.workflow.yaml
      agent-review.workflow.yaml
      retrieval-pipeline.workflow.yaml
    tests/
      test_wdf_nodes.py      # Node config schema tests
      test_wdf_workflow.py    # WorkflowDefinition / EdgeDefinition tests
      test_wdf_variable_ref.py # Variable reference extraction tests
      test_wdf_examples.py   # Validates example YAML files parse correctly
  scripts/
    release/
      validate_release.py     # Semver + tag/version validation
  tests/
    conftest.py               # Shared test fixtures
    test_client.py            # WorkflowClient unit tests (pytest-httpx)
    test_exceptions.py        # API exception hierarchy tests
    test_lockfile.py          # Lockfile management tests (30 tests)
    test_push_command.py      # Push command unit tests (20 tests)
    test_release_validation.py # Release validation coverage
    test_shared_models_integration.py
    test_wdf_yaml_roundtrip.py # WDF YAML round-trip serialization tests
  docs/
    codeartifact.md           # CodeArtifact setup, publishing, CI/CD
    init-command.md           # init command documentation
    validate-command.md       # validate command documentation
    push-command.md           # push command documentation
  .pre-commit-config.yaml     # Pre-commit hook configuration
  README.md                   # This file
```

## Usage

```bash
# Show help
uv run workflow --help

# Show version
uv run workflow --version

# Scaffold a new workflow from a template
uv run workflow init --list
uv run workflow init --template rag-qa

# Validate a workflow file
uv run workflow validate my-workflow.workflow.yaml

# Push a workflow to the platform
uv run workflow push my-workflow.workflow.yaml
```

### Commands

| Command | Description | Docs |
|---------|-------------|------|
| `init` | Scaffold a new workflow from a template | [`docs/init-command.md`](docs/init-command.md) |
| `validate` | Validate a workflow definition file offline | [`docs/validate-command.md`](docs/validate-command.md) |
| `push` | Deploy a workflow to the platform (create or update) | [`docs/push-command.md`](docs/push-command.md) |

## API Client

The CLI includes a typed HTTP client (`WorkflowClient`) for communicating with
the Agents Platform REST API. It uses **httpx** (synchronous mode) with
`X-API-Key` authentication, and returns validated **Pydantic models** from the
shared-models package.

### Quick Start

```python
from cli.client import WorkflowClient

# Direct construction
with WorkflowClient(
    host="https://api.example.com",
    api_key="your-api-key",
    org_id="your-org-id",
) as client:
    workflows = client.list_workflows()
    workflow = client.get_workflow(workflow_id)

# Or from CLI configuration (validates required fields)
from cli.config import CLIConfig

config = CLIConfig.load()
client = WorkflowClient.from_config(config)
```

### Available Methods

#### V1 Workflow CRUD

| Method | Description | Returns |
|--------|-------------|---------|
| `list_workflows()` | List workflows with pagination | `list[WorkflowPublic]` |
| `get_workflow(id)` | Get a single workflow | `WorkflowPublic` |
| `delete_workflow(id)` | Delete a workflow | `None` |
| `list_nodes(workflow_id)` | List nodes for a workflow | `list[LogicalNodePublic]` |
| `list_edges(workflow_id)` | List edges for a workflow | `list[LogicalEdgePublic]` |
| `get_metadata(workflow_id)` | Get workflow metadata | `WorkflowMetadataPublic` |

#### V1 Atomic Save

| Method | Description | Returns |
|--------|-------------|---------|
| `save_complete_workflow(payload)` | Create or update a full workflow atomically | `SaveCompleteWorkflowResponse` |

#### V2 Temporal Execution

| Method | Description | Returns |
|--------|-------------|---------|
| `start_workflow_temporal(id, inputs)` | Start a workflow via Temporal | `TemporalStartResponse` |
| `get_workflow_status(id, run_id)` | Check execution status | `WorkflowStatusResponse` |
| `submit_input(id, run_id, node_id, data)` | Submit input to a paused INPUT node | `SubmitInputResponse` |
| `submit_review(id, run_id, decision)` | Submit a HUMAN_REVIEW decision | `SubmitReviewResponse` |

#### Dependency Resolution

| Method | Description | Returns |
|--------|-------------|---------|
| `find_agent_by_name(name)` | Find agent by name (case-insensitive) | `dict \| None` |
| `find_knowledge_base_by_name(name)` | Find KB by name (case-insensitive) | `dict \| None` |

### Exception Handling

All API errors are mapped to typed exceptions with status codes and error details:

```python
from cli.exceptions import (
    APIError,            # Base class for all API errors
    AuthenticationError, # 401 — invalid or missing API key
    AuthorizationError,  # 403 — insufficient permissions
    NotFoundError,       # 404 — resource not found
    ValidationError,     # 400/422 — invalid request payload
    ConflictError,       # 409 — state conflict
    RateLimitError,      # 429 — rate limit exceeded (has retry_after)
    ServerError,         # 500/502/503 — server-side failure
)

try:
    workflow = client.get_workflow("nonexistent-id")
except NotFoundError as e:
    print(f"Status {e.status_code}: {e.detail}")
except APIError as e:
    print(f"Unexpected error: {e}")
```

### Testing

```bash
# Run API client tests
uv run pytest tests/test_client.py tests/test_exceptions.py -v

# All tests (includes client + exceptions + WDF + release)
uv run pytest
```

## Workflow Definition Format (WDF)

The CLI supports a **YAML-based Workflow Definition Format** for authoring
workflows as human-readable files. WDF files use slug-keyed nodes, variable
references (`{{slug.output.field}}`), and typed edge definitions — no UUIDs
required.

The **Pydantic models** (validation, type checking) live in the shared-models
package (`workflow_models.wdf`). The **YAML layer** (load/dump via PyYAML) lives
in the CLI (`cli.wdf_yaml`), keeping shared-models free of PyYAML runtime
dependencies.

### YAML Round-Trip

```python
from cli.wdf_yaml import load_workflow, dump_workflow

# Load a .workflow.yaml file into a validated WorkflowDefinition
workflow = load_workflow('path/to/my.workflow.yaml')

# Dump back to YAML string
yaml_str = dump_workflow(workflow)
```

### Example Workflow Files

See `shared-models/examples/` for reference YAML files:

- `invoice-processing.workflow.yaml` — realistic 4-node invoice processing pipeline
- `all-node-types.workflow.yaml` — reference file demonstrating all 10 node types
- `linear-pipeline.workflow.yaml` — 3-node pipeline matching backend `linear_pipeline.json`
- `rag-workflow.workflow.yaml` — 4-node RAG pipeline matching backend `rag_workflow.json`
- `agent-review.workflow.yaml` — 4-node agent + human review pipeline matching backend `agent_review.json`
- `retrieval-pipeline.workflow.yaml` — 5-node retrieval pipeline matching backend `retrieval_pipeline.json`

### Testing WDF

```bash
# Shared-models tests (Pydantic models, validation, variable refs, example files)
uv run pytest shared-models/tests/test_wdf_*.py -v

# CLI tests (YAML round-trip serialization)
uv run pytest tests/test_wdf_yaml_roundtrip.py -v

# All tests
uv run pytest
```

## Lockfile Management

The CLI includes lockfile support (`.workflow.lock`) for tracking the mapping between
local workflow slugs and server-side UUIDs. This enables **idempotent push operations**
— subsequent pushes update in place rather than creating duplicates.

**See [`docs/push-command.md`](docs/push-command.md)** for full details on how lockfiles
are used in the push workflow.

### Lockfile Format

The lockfile is auto-generated alongside your `.workflow.yaml` file with the same
base name but `.lock` extension. For example, `my-workflow.workflow.yaml` generates
`my-workflow.workflow.lock`.

```yaml
# Auto-generated by workflow CLI. Do not edit manually.
workflow_id: 3fa85f64-5717-4562-b3fc-2c963f66afa6
organization_id: 9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d
version: 1
instance: https://api.example.com
nodes:
  upload: a1b2c3d4-e5f6-7890-abcd-ef1234567890
  extract: b2c3d4e5-f6a7-8901-bcde-f12345678901
edges:
  upload->extract: 1001
pushed_at: 2026-02-15T14:30:00Z
```

### Usage (Integration with Push Command)

```python
from pathlib import Path
from cli.lockfile import load_lockfile, save_lockfile, update_lockfile, get_lockfile_path

workflow_path = Path('my-workflow.workflow.yaml')

# Before push: check if lockfile exists (determines create vs. update mode)
existing_lock = load_lockfile(get_lockfile_path(workflow_path))
if existing_lock:
    # UPDATE mode: use existing workflow_id and node/edge UUIDs
    workflow_id = existing_lock.workflow_id
    node_uuid = existing_lock.get_node_uuid('upload')  # Returns UUID or None
else:
    # CREATE mode: generate new UUIDs
    workflow_id = None

# After successful push: save/update lockfile with server-assigned UUIDs
from datetime import datetime, UTC
from uuid import UUID

lock = WorkflowLock(
    workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
    organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
    version=1,
    instance='https://api.example.com',
    pushed_at=datetime.now(UTC),
)
lock.set_node_uuid('upload', UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890'))
lock.set_edge_id('upload', 'extract', 1001)

save_lockfile(workflow_path, lock)
```

### Testing Lockfile

```bash
# Run lockfile tests (30 unit tests)
uv run pytest tests/test_lockfile.py -v

# Test coverage includes:
# - WorkflowLock Pydantic model validation
# - Read/write/update operations
# - Path utilities and edge cases
# - Invalid YAML and schema validation
```
