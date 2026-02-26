"""workflow review command implementation.

Submits a human review decision (approve/reject/revise) to a paused
HUMAN_REVIEW node in a running workflow execution.

Features:
- Exactly one of --approve, --reject, or --revise required
- --comment required with --reject and --revise
- Pre-flight validation: fetches status to confirm node is a paused HUMAN_REVIEW node
- Does NOT send node_id to the API (V2 review API auto-targets the paused review node)
- Interactive Y/N confirmation before submitting
- --json flag for machine-readable output
- Minimal success messages: "Approved.", "Rejected.", "Revision requested."

Usage:
    workflow review --run-id abc --node-id def --approve
    workflow review --run-id abc --node-id def --reject --comment "Needs fixes"
    workflow review --run-id abc --node-id def --revise --comment "Update section 3"
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.prompt import Confirm

from cli.client import WorkflowClient
from cli.commands.status import _resolve_run_context
from cli.config import CLIConfig
from cli.console import get_console

# Decision label mapping for confirmation prompt and success message
_DECISION_LABELS: dict[str, str] = {
    'approve': 'Approved.',
    'reject': 'Rejected.',
    'revise': 'Revision requested.',
}


def review_command(
    config: CLIConfig,
    *,
    run_id: str,
    node_id: str,
    approve: bool = False,
    reject: bool = False,
    revise: bool = False,
    comment: str | None = None,
    json_output: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Submit a human review decision to a paused HUMAN_REVIEW node.

    Validates the decision flags, resolves run context (requires explicit
    --run-id), performs pre-flight validation that the target node is a
    paused HUMAN_REVIEW node, confirms with the user, and calls the API.

    Args:
        config: CLI configuration with API credentials.
        run_id: Explicit run ID (required for review).
        node_id: ID of the HUMAN_REVIEW node to validate against.
        approve: If True, submit an approval.
        reject: If True, submit a rejection.
        revise: If True, request revision.
        comment: Feedback text (required with reject/revise).
        json_output: If True, print raw JSON response and return.
        working_dir: Directory for .last_run lookup (defaults to cwd).

    Raises:
        ValueError: If flags are invalid, node is not a paused HUMAN_REVIEW,
            or run context cannot be resolved.
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()
    console = get_console()

    # Validate exactly one decision flag
    chosen = [
        flag for flag, val in [('approve', approve), ('reject', reject), ('revise', revise)] if val
    ]
    if len(chosen) != 1:
        raise ValueError('Specify exactly one of --approve, --reject, or --revise.')
    decision = chosen[0]

    # Reject/revise require --comment
    if decision in ('reject', 'revise') and not comment:
        raise ValueError('--comment is required with --reject and --revise.')

    # Resolve run context (require_explicit=True: --run-id is mandatory)
    workflow_id, resolved_run_id = _resolve_run_context(run_id, cwd, require_explicit=True)

    # Pre-flight validation: confirm the specified node is a paused HUMAN_REVIEW node
    with WorkflowClient.from_config(config) as client:
        status_resp = client.get_workflow_status(workflow_id, resolved_run_id)
        nodes = client.list_nodes(workflow_id)

        # Check that the node_id exists and is a HUMAN_REVIEW type
        node_type_map: dict[str, str] = {}
        for node in nodes:
            nid = str(node.id)
            config_type = (
                node.config_type.value
                if hasattr(node.config_type, 'value')
                else str(node.config_type)
            )
            node_type_map[nid] = config_type

        if node_id not in node_type_map:
            raise ValueError(f'Node {node_id} not found in workflow {workflow_id}.')

        if node_type_map[node_id] != 'HUMAN_REVIEW':
            raise ValueError(f'Node {node_id} is type {node_type_map[node_id]}, not HUMAN_REVIEW.')

        # Check that this node is actually paused for review
        state = status_resp.state
        review_node_id = state.get('review_node_id')
        if review_node_id != node_id:
            # Find the actual review node if one exists
            actual_review = review_node_id or 'none'
            raise ValueError(
                f'Node {node_id} is not currently paused for review. '
                f'Current review node: {actual_review}'
            )

        # Confirmation prompt
        confirmed = Confirm.ask(f'Submit review: {decision}?', default=True)
        if not confirmed:
            console.print('[yellow]Cancelled.[/yellow]')
            return

        # Call the API (no node_id -- V2 API auto-targets paused review node)
        resp = client.submit_review(
            workflow_id,
            run_id=resolved_run_id,
            decision=decision,
            feedback=comment,
        )

    # Output
    if json_output:
        print(json.dumps(resp.model_dump(), indent=2))
        return

    console.print(f'[green]{_DECISION_LABELS[decision]}[/green]')
