"""workflow input command implementation.

Submits data to a paused INPUT node in a running workflow execution.

Features:
- Parse --data as inline JSON string or @filepath (reuses parse_input_arg)
- .last_run context for workflow_id/run_id with explicit --run-id override
- Interactive Y/N confirmation before submitting
- --json flag for machine-readable output
- Minimal success message: "Input submitted."

Usage:
    workflow input --node-id abc-123 --data '{"text": "Hello"}'
    workflow input --node-id abc-123 --data @input.json
    workflow input --node-id abc-123 --data '{"text": "Hello"}' --json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm

from cli.client import WorkflowClient
from cli.commands.run import parse_input_arg
from cli.commands.status import _resolve_run_context
from cli.config import CLIConfig

console = Console()


def input_command(
    config: CLIConfig,
    node_id: str,
    data: str | None = None,
    *,
    run_id: str | None = None,
    json_output: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Submit data to a paused INPUT node.

    Resolves workflow/run context from .last_run (or explicit --run-id),
    parses the data payload, confirms with the user, and calls the API.

    Args:
        config: CLI configuration with API credentials.
        node_id: ID of the INPUT node to submit data to.
        data: Input data as JSON string or @filepath (None -> empty dict).
        run_id: Optional explicit run ID (overrides .last_run run_id).
        json_output: If True, print raw JSON response and return.
        working_dir: Directory for .last_run lookup (defaults to cwd).

    Raises:
        ValueError: If run context cannot be resolved or data is invalid JSON.
        FileNotFoundError: If @filepath doesn't exist.
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()

    # Resolve workflow_id and run_id from .last_run or explicit override
    workflow_id, resolved_run_id = _resolve_run_context(run_id, cwd)

    # Parse data payload (handles None, JSON string, @filepath)
    data_dict: dict[str, Any] = parse_input_arg(data)

    # Confirmation prompt
    confirmed = Confirm.ask(f'Submit input to node {node_id}?', default=True)
    if not confirmed:
        console.print('[yellow]Cancelled.[/yellow]')
        return

    # Call the API
    with WorkflowClient.from_config(config) as client:
        resp = client.submit_input(
            workflow_id,
            run_id=resolved_run_id,
            node_id=node_id,
            input_data=data_dict,
        )

    # Output
    if json_output:
        print(json.dumps(resp.model_dump(), indent=2))
        return

    console.print('[green]Input submitted.[/green]')
