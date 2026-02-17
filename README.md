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
  pyproject.toml            # Package configuration (uv-managed)
  src/
    cli/
      __init__.py           # Package init with version
      __main__.py           # Enables `python -m cli`
      main.py               # Typer app entry point
  tests/
    conftest.py             # Shared test fixtures
  .pre-commit-config.yaml   # Pre-commit hook configuration
  README.md                 # This file
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
