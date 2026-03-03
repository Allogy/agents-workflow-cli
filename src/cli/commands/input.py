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

from rich.prompt import Confirm

from cli.client import WorkflowClient
from cli.commands.run import parse_input_arg
from cli.commands.status import _resolve_run_context
from cli.config import CLIConfig
from cli.console import get_console


def input_command(
    config: CLIConfig,
    node_id: str,
    data: str | None = None,
    *,
    run_id: str | None = None,
    json_output: bool = False,
    working_dir: Path | None = None,
    workflow_id_override: str | None = None,
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
        workflow_id_override: Explicit workflow ID (overrides .last_run). Requires run_id.

    Raises:
        ValueError: If run context cannot be resolved or data is invalid JSON.
        FileNotFoundError: If @filepath doesn't exist.
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()

    # Resolve workflow_id and run_id from .last_run or explicit override
    workflow_id, resolved_run_id = _resolve_run_context(
        run_id, cwd, workflow_id_override=workflow_id_override
    )

    # Parse data payload (handles None, JSON string, @filepath)
    data_dict: dict[str, Any] = parse_input_arg(data)

    console = get_console()

    with WorkflowClient.from_config(config) as client:
        # Pre-flight validation: check workflow state before prompting
        status_resp = client.get_workflow_status(workflow_id, resolved_run_id)
        nodes = client.list_nodes(workflow_id)

        # Build node-type map (same pattern as review.py)
        node_type_map: dict[str, str] = {}
        for node in nodes:
            nid = str(node.id)
            config_type = (
                node.config_type.value
                if hasattr(node.config_type, 'value')
                else str(node.config_type)
            )
            node_type_map[nid] = config_type

        # Check node exists in this workflow
        if node_id not in node_type_map:
            raise ValueError(f'Node {node_id} not found in workflow {workflow_id}.')

        # Check terminal states
        overall_status = status_resp.status.upper()
        state = status_resp.state
        if overall_status in ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT'):
            raise ValueError(
                f'Workflow has {overall_status.lower()}. '
                f"Use 'workflow status' to see final results."
            )

        # Check if workflow is paused at a review node instead
        if state.get('review_node_id'):
            review_node_id = state.get('review_node_id')
            raise ValueError(
                f'Workflow is paused for review at node {review_node_id}. '
                f"Use 'workflow review --run-id {resolved_run_id} "
                f"--node-id {review_node_id} --approve' instead."
            )

        # Check the target node is actually waiting for input
        waiting_node = state.get('waiting_input_node_id')
        # Fallback: use current_node_id when waiting_input_node_id is missing
        # but execution_status indicates workflow is waiting for input (BUG-1 defensive)
        if not waiting_node and state.get('execution_status', '').upper() == 'WAITING_FOR_INPUT':
            waiting_node = state.get('current_node_id')

        if waiting_node != node_id:
            actual = waiting_node or 'none'
            raise ValueError(
                f'Node {node_id} is not currently waiting for input. Current input node: {actual}'
            )

        # Confirmation prompt
        confirmed = Confirm.ask(f'Submit input to node {node_id}?', default=True)
        if not confirmed:
            console.print('[yellow]Cancelled.[/yellow]')
            return

        # Call the API
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
