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
  bitbucket-pipelines.yml     # CI/CD — publish to CodeArtifact on version tags
  src/
    cli/
      __init__.py             # Package init with version
      __main__.py             # Enables `python -m cli`
      main.py                 # Typer app entry point
      wdf_yaml.py             # WDF YAML load/dump helpers (PyYAML wrapper)
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
    test_release_validation.py # Release validation coverage
    test_shared_models_integration.py
    test_wdf_yaml_roundtrip.py # WDF YAML round-trip serialization tests
  docs/
    codeartifact.md           # CodeArtifact setup, publishing, CI/CD
  .pre-commit-config.yaml     # Pre-commit hook configuration
  README.md                   # This file
```

## Usage

```bash
# Show help
uv run workflow --help

# Show version
uv run workflow --version

# Run a command
uv run workflow hello
uv run workflow hello "Agents Platform"
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

### Testing WDF

```bash
# Shared-models tests (Pydantic models, validation, variable refs, example files)
uv run pytest shared-models/tests/test_wdf_*.py -v

# CLI tests (YAML round-trip serialization)
uv run pytest tests/test_wdf_yaml_roundtrip.py -v

# All tests
uv run pytest
```
