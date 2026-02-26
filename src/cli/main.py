"""Workflow CLI — main entry point."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cli import __version__
from cli.commands.delete import delete_command
from cli.commands.init import init_command
from cli.commands.input import input_command
from cli.commands.list import list_command
from cli.commands.pull import pull_workflow
from cli.commands.push import push_workflow
from cli.commands.review import review_command
from cli.commands.run import run_command
from cli.commands.status import status_command
from cli.commands.validate import validate_command
from cli.config import CLIConfig, load_config, resolve_config

app = typer.Typer(
    name='workflow',
    help='CLI tool for managing and executing workflows on the Agents Platform.',
    no_args_is_help=True,
    rich_markup_mode='rich',
)

# Module-level state (set by the callback)
_config: CLIConfig | None = None
_no_color: bool = False


def get_config() -> CLIConfig:
    """Return the current resolved CLI config. Raises if not yet initialized."""
    if _config is None:
        return load_config()
    return _config


def get_console() -> Console:
    """Return a Rich Console respecting the global --no-color flag.

    Rich Console natively honours the NO_COLOR environment variable,
    so only the explicit CLI flag needs handling here.
    """
    return Console(no_color=True) if _no_color else Console()


class FormatChoice(str, Enum):
    """Output format choices for the --format option."""

    json = 'json'
    yaml = 'yaml'


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        get_console().print(f'workflow-cli v{__version__}')
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            '--version',
            '-v',
            help='Show the CLI version and exit.',
            callback=version_callback,
            is_eager=True,
        ),
    ] = False,
    host: Annotated[
        str | None,
        typer.Option(
            '--host',
            help='API host URL (overrides WORKFLOW_API_HOST env var).',
            envvar='WORKFLOW_API_HOST',
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            '--api-key',
            help='API authentication key (overrides WORKFLOW_API_KEY env var).',
            envvar='WORKFLOW_API_KEY',
        ),
    ] = None,
    org: Annotated[
        str | None,
        typer.Option(
            '--org',
            help='Organization ID (overrides WORKFLOW_ORG_ID env var).',
            envvar='WORKFLOW_ORG_ID',
        ),
    ] = None,
    output_format: Annotated[
        FormatChoice | None,
        typer.Option(
            '--format',
            help='Output format: json or yaml.',
        ),
    ] = None,
    no_color: Annotated[
        bool,
        typer.Option(
            '--no-color',
            help='Disable colored output.',
        ),
    ] = False,
) -> None:
    """Agents Platform Workflow CLI."""
    global _config, _no_color  # noqa: PLW0603

    _no_color = no_color

    # Load base config from file + env vars, then apply CLI flag overrides
    base = load_config()
    _config = resolve_config(
        base,
        host=host,
        api_key=api_key,
        org_id=org,
        output_format=output_format.value if output_format else None,
    )


@app.command()
def validate(
    file_path: Annotated[
        Path,
        typer.Argument(
            help='Path to .workflow.yaml file to validate.',
            exists=False,  # We handle existence check in the command
        ),
    ],
) -> None:
    """Validate a workflow definition file offline.

    Runs 9 validation checks with no API calls:
    - YAML syntax
    - WDF schema conformance
    - Node type recognition
    - Edge references
    - Entry/exit points
    - Graph reachability
    - Cycle detection
    - Variable references
    - Node config validation

    Exit codes: 0 = pass (warnings OK), 1 = failure
    """
    validate_command(file_path)


@app.command()
def init(
    template: Annotated[
        str | None,
        typer.Option(
            '--template',
            '-t',
            help='Template name to scaffold from (e.g. rag-qa, simple-form).',
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            '--output',
            '-o',
            help='Output file path. Defaults to {template-name}.workflow.yaml.',
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            '--force',
            help='Overwrite existing file if it exists.',
        ),
    ] = False,
    list_templates: Annotated[
        bool,
        typer.Option(
            '--list',
            help='List all available templates and exit.',
        ),
    ] = False,
) -> None:
    """Scaffold a new workflow from a built-in template.

    Create a .workflow.yaml file from one of the built-in templates.
    Use --list to see available templates, or --template to pick one.

    Examples:
        workflow init --list
        workflow init --template rag-qa
        workflow init --template simple-form -o my-workflow.workflow.yaml
    """
    init_command(template=template, output=output, force=force, list_mode=list_templates)


@app.command()
def push(
    file_path: Annotated[
        Path,
        typer.Argument(
            help='Path to .workflow.yaml file to push.',
            exists=True,
        ),
    ],
) -> None:
    """Push a workflow to the platform.

    Creates or updates a workflow on the platform using the atomic save endpoint.
    Automatically resolves dependencies (agents, knowledge bases) and generates
    node layout positions.

    On first push, creates a .workflow.lock file to track server-side UUIDs.
    Subsequent pushes use the lockfile to update the existing workflow in place.

    Exit codes: 0 = success, 1 = failure

    Examples:
        workflow push my-workflow.workflow.yaml
        workflow push --host https://api.example.com --api-key xxx workflow.yaml
    """
    config = get_config()
    try:
        push_workflow(file_path, config)
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}')
        raise typer.Exit(1) from e


@app.command()
def pull(
    identifier: Annotated[
        str,
        typer.Argument(
            help='Workflow UUID or name to pull.',
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            '--output',
            '-o',
            help='Output file path. Defaults to <slugified-name>.workflow.yaml.',
        ),
    ] = None,
) -> None:
    """Pull a workflow from the platform to a local YAML file.

    Exports a workflow to a .workflow.yaml definition file and generates a
    .workflow.lock file for round-trip push/pull.

    Supports pulling by UUID (exact) or by name (fuzzy matching with
    interactive selection when multiple matches are found).

    Agent and knowledge base UUIDs are reverse-resolved to human-readable
    names. Visual-only data (node positions, edge paths) is stripped.

    Exit codes: 0 = success, 1 = failure

    Examples:
        workflow pull abc123-def456-...
        workflow pull abc123 -o invoices.workflow.yaml
        workflow pull "Invoice Processing"
        workflow pull "Invoice" --host https://api.example.com --api-key xxx
    """
    config = get_config()
    try:
        pull_workflow(identifier, config, output)
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}')
        raise typer.Exit(1) from e


@app.command()
def list(
    output_format: Annotated[
        FormatChoice | None,
        typer.Option(
            '--format',
            help='Output format: json, yaml, or table (default).',
        ),
    ] = None,
) -> None:
    """List workflows in the organization.

    Displays all workflows accessible to your organization with various
    output formats for different use cases.

    Table format (default):
    - Human-readable with columns: Name, ID (truncated), Updated
    - Includes rich formatting and colors

    JSON format:
    - Machine-readable output suitable for piping to other tools
    - Contains full workflow metadata

    YAML format:
    - Human-readable structured output
    - Good for documentation or review

    Exit codes: 0 = success, 1 = failure

    Examples:
        workflow list
        workflow list --format json
        workflow list --format yaml | grep "name:"
    """
    config = get_config()
    try:
        list_command(config, output_format.value if output_format else None)
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}')
        raise typer.Exit(1) from e


@app.command()
def delete(
    identifier: Annotated[
        str,
        typer.Argument(
            help='Workflow UUID or name to delete.',
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            '--force',
            '-f',
            help='Skip confirmation prompt.',
        ),
    ] = False,
) -> None:
    """Delete a workflow by ID or name.

    Removes a workflow from the platform. Can identify workflows by either
    their UUID or name (fuzzy matching supported).

    By default, prompts for confirmation before deletion. Use --force to
    skip the confirmation prompt.

    Workflow name matching:
    - Exact match (case-insensitive) is preferred
    - Falls back to partial match if only one workflow contains the search term
    - Returns error if multiple matches found (use UUID for precision)

    Exit codes: 0 = success or cancelled, 1 = failure

    Examples:
        workflow delete abc123-def456-...
        workflow delete "Invoice Processing"
        workflow delete "customer onboarding" --force
        workflow delete abc123 --force
    """
    config = get_config()
    try:
        delete_command(config, identifier, force)
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}')
        raise typer.Exit(1) from e


@app.command()
def run(
    identifier: Annotated[
        str,
        typer.Argument(
            help='Workflow UUID or name to execute.',
        ),
    ],
    input_data: Annotated[
        str | None,
        typer.Option(
            '--input',
            '-i',
            help='Initial input data as JSON string or @filepath.',
        ),
    ] = None,
    stream: Annotated[
        bool,
        typer.Option(
            '--stream',
            help='Use SSE streaming instead of polling.',
        ),
    ] = False,
    no_follow: Annotated[
        bool,
        typer.Option(
            '--no-follow',
            help='Start the workflow and exit immediately.',
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            '--verbose',
            help='Show detailed multi-line SSE output with payload excerpts.',
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            '--no-color',
            help='Disable colored output (also respects NO_COLOR env var).',
        ),
    ] = False,
) -> None:
    """Execute a workflow via Temporal runtime.

    Starts a workflow execution and displays node-by-node progress.
    Supports execution by UUID or workflow name.

    Default mode polls for status every 2 seconds. Use --stream for
    real-time SSE event display. Use --no-follow to start and exit
    immediately.

    Use --verbose with --stream for detailed multi-line output with
    payload excerpts. Use --no-color to disable colored output
    (for CI pipelines or piped output).

    When a human-in-the-loop gate is reached (INPUT or REVIEW node),
    the command exits with a hint for the next action.

    A .workflow.last_run context file is written for use by subsequent
    workflow status/input/review commands.

    Exit codes: 0 = success or HITL pause, 1 = runtime error, 2 = user error

    Examples:
        workflow run 939843a8-6257-4475-bfc0-f7d6500d9f00
        workflow run "Invoice Processing" --input '{"question": "What is AI?"}'
        workflow run my-workflow --input @input.json --stream
        workflow run my-workflow --stream --verbose
        workflow run my-workflow --no-follow
        workflow run my-workflow --stream --no-color
    """
    config = get_config()
    try:
        run_command(
            config,
            identifier,
            input_data,
            stream=stream,
            no_follow=no_follow,
            verbose=verbose,
            no_color=no_color,
        )
    except (ValueError, FileNotFoundError) as e:
        # User errors: bad input, not found, invalid JSON -> exit 2
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(2) from e
    except Exception as e:
        # Runtime errors: network, server, timeout -> exit 1
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(1) from e


@app.command()
def status(
    run_id: Annotated[
        str | None,
        typer.Argument(
            help='Run ID to check status for. Uses .last_run if omitted.',
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            '--json',
            help='Output raw JSON response.',
        ),
    ] = False,
) -> None:
    """Check workflow execution status with node-by-node detail.

    Shows overall workflow state and a per-node breakdown with IDs,
    types, and statuses. Paused nodes are highlighted with actionable
    hints for the next command.

    Uses .workflow.last_run context by default. Pass a run ID as
    argument to override, or use --json for machine-readable output.

    Exit codes: 0 = success, 1 = runtime error, 2 = user error

    Examples:
        workflow status
        workflow status a1b2c3d4-e5f6-7890-abcd-ef1234567890
        workflow status --json
    """
    config = get_config()
    try:
        status_command(config, run_id, json_output=json_output)
    except (ValueError, FileNotFoundError) as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(2) from e
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(1) from e


@app.command(name='input')
def input_cmd(
    node_id: Annotated[
        str,
        typer.Option(
            '--node-id',
            help='ID of the INPUT node to submit data to.',
        ),
    ],
    data: Annotated[
        str | None,
        typer.Option(
            '--data',
            '-d',
            help='Input data as JSON string or @filepath.',
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option(
            '--run-id',
            help='Run ID (overrides .last_run context).',
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            '--json',
            help='Output raw JSON response.',
        ),
    ] = False,
) -> None:
    """Submit data to a paused INPUT node.

    Sends input data to a workflow node that is waiting for human input.
    Uses .workflow.last_run for workflow/run context by default.

    The --data flag accepts inline JSON or @filepath syntax:
        --data '{"key": "value"}'
        --data @input.json

    Requires interactive Y/N confirmation before sending.

    Exit codes: 0 = success or cancelled, 1 = runtime error, 2 = user error

    Examples:
        workflow input --node-id abc-123 --data '{"text": "Hello"}'
        workflow input --node-id abc-123 --data @input.json
        workflow input --node-id abc-123 --data '{"text": "Hello"}' --json
    """
    config = get_config()
    try:
        input_command(config, node_id, data, run_id=run_id, json_output=json_output)
    except (ValueError, FileNotFoundError) as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(2) from e
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(1) from e


@app.command()
def review(
    run_id: Annotated[
        str,
        typer.Option(
            '--run-id',
            help='Run ID (required for review).',
        ),
    ],
    node_id: Annotated[
        str,
        typer.Option(
            '--node-id',
            help='ID of the HUMAN_REVIEW node.',
        ),
    ],
    approve: Annotated[
        bool,
        typer.Option(
            '--approve',
            help='Approve the review.',
        ),
    ] = False,
    reject: Annotated[
        bool,
        typer.Option(
            '--reject',
            help='Reject the review.',
        ),
    ] = False,
    revise: Annotated[
        bool,
        typer.Option(
            '--revise',
            help='Request revision.',
        ),
    ] = False,
    comment: Annotated[
        str | None,
        typer.Option(
            '--comment',
            '-c',
            help='Comment/feedback (required with --reject and --revise).',
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            '--json',
            help='Output raw JSON response.',
        ),
    ] = False,
) -> None:
    """Submit a human review decision to a paused HUMAN_REVIEW node.

    Validates that the specified node is actually a paused HUMAN_REVIEW
    node before submitting. Requires both --run-id and --node-id.

    Exactly one of --approve, --reject, or --revise must be specified.
    The --reject and --revise flags require --comment.

    Requires interactive Y/N confirmation before sending.

    Exit codes: 0 = success or cancelled, 1 = runtime error, 2 = user error

    Examples:
        workflow review --run-id abc --node-id def --approve
        workflow review --run-id abc --node-id def --reject --comment "Needs fixes"
        workflow review --run-id abc --node-id def --revise --comment "Update section 3"
        workflow review --run-id abc --node-id def --approve --json
    """
    config = get_config()
    try:
        review_command(
            config,
            run_id=run_id,
            node_id=node_id,
            approve=approve,
            reject=reject,
            revise=revise,
            comment=comment,
            json_output=json_output,
        )
    except (ValueError, FileNotFoundError) as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(2) from e
    except Exception as e:
        get_console().print(f'[bold red]Error:[/bold red] {e}', highlight=False)
        raise typer.Exit(1) from e
