# CLAUDE.md — Workflow CLI

## Project Overview

CLI tool + shared Pydantic models for workflow management on the Capillary Actions platform.
Two packages: `agents-workflow-cli` (CLI) and `agents-workflow-models` (shared models).
Tech stack: Python 3.13, Typer, Rich, httpx.

## Essential Commands

```bash
uv sync --all-groups                          # Install all dependencies
uv run workflow --help                        # CLI usage
uv run workflow validate <file.yaml>          # Offline validation (9 checks)
uv run workflow push <file.yaml>              # Deploy with lockfile
uv run workflow pull <uuid-or-name>           # Export to YAML
uv run workflow init                          # Scaffold from templates
uv run workflow list                          # List workflows
uv run workflow delete <uuid-or-name>         # Delete workflow
uv run ruff check .                           # Lint
uv run ruff format .                          # Format
uv run pytest                                 # Run tests

# CodeArtifact (from root Makefile)
make codeartifact-setup                       # First-time setup
make codeartifact-login                       # Refresh token
make codeartifact-publish-cli                 # Publish CLI package
make codeartifact-publish-models              # Publish shared models
```

## Architecture

For deep-dives, read the referenced docs:
- `docs/init-command.md` — scaffold workflows from 7 templates
- `docs/validate-command.md` — 9 offline validation checks
- `docs/push-command.md` — deploy with lockfile and dependency resolution
- `docs/pull-command.md` — export workflows to YAML
- `docs/codeartifact.md` — CodeArtifact publishing guide
- `shared-models/README.md` — shared Pydantic models package

### Directory Structure

```
src/cli/
  main.py              # Typer app entry point
  client.py            # WorkflowClient (httpx + X-API-Key)
  config.py            # Config loading (flags → env → file → defaults)
  lockfile.py          # .workflow.lock read/write
  wdf_yaml.py          # WDF YAML parsing/serialization
  exceptions.py        # CLI-specific exceptions
  commands/             # One module per CLI command (init, validate, push, pull, list, delete)
  validation/           # Validation runner (9 offline checks)
  templates/            # Scaffold templates for `workflow init`

shared-models/src/workflow_models/
  enums.py             # Shared enumerations
  schemas/             # API schemas (workflows, nodes, edges, execution, visuals, metadata)
  wdf/                 # WDF models (workflow, nodes, edges, validation, variable_ref)
```

### Key Conventions

- **WDF (Workflow Definition Format):** Human-authored YAML with slug-based node references, variable templates (`{{slug.output.field}}`), and 12 node types.
- **Lockfile (`.workflow.lock`):** Tracks slug-to-UUID mappings for idempotent deploys. 3-tier dependency resolution: UUID passthrough, lockfile cache, API lookup.
- **API client:** `WorkflowClient` using httpx with `X-API-Key` auth. Config precedence: CLI flags, env vars, `~/.workflow/config.yaml`, defaults.
- **Shared models (`shared-models/`):** Pure Pydantic v2 with zero SQLAlchemy dependency. Contains API schemas (`schemas/`) and WDF models (`wdf/`). Published as `agents-workflow-models` to CodeArtifact.
- **Entry point:** `cli.main:app` (Typer application). Installed as `workflow` command via `[project.scripts]`.

## Code Style

Ruff with `line-length = 100`, `target-version = "py313"`, single quotes.

**Lint rules:** E, W, F, I, B, UP.
**Ignored:** B008 (Typer defaults), UP042 (Typer/shared-models str+Enum compat), E501 (formatter handles line length).
**First-party imports:** `cli`.

Full type annotations required. Absolute imports only — never relative.

## Critical Rules

- **Always `uv`** — NEVER pip.
- **`shared-models` must stay pure Pydantic v2** — zero SQLAlchemy/SQLModel dependency.
- **Lockfile (`.workflow.lock`) must be committed** — tracks slug-to-UUID mappings for idempotent deploys.
- **CodeArtifact publishing:** bump version in `pyproject.toml` before publishing.
- **Context7 MCP:** always use for library/API documentation when needed.
- **This is a submodule** — commit here first, then update the reference in the parent repo.

## Before Committing

1. `uv run ruff check . && uv run ruff format .`
2. `uv run pytest`
3. Pre-commit hooks auto-run (ruff format, trailing whitespace).
