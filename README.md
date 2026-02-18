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
  shared-models/              # agents-workflow-models (published separately)
    pyproject.toml
    src/workflow_models/
  scripts/
    release/
      validate_release.py     # Semver + tag/version validation
  tests/
    conftest.py               # Shared test fixtures
    test_release_validation.py # Release validation coverage
    test_shared_models_integration.py
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
