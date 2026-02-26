"""workflow status command implementation.

Shows overall workflow execution state and a per-node status breakdown.

Features:
- Status by .last_run context (default) or explicit run-id
- Unified table: overall state + per-node rows with IDs, types, statuses
- Paused-node visual markers and actionable hints
- --json flag for machine-readable output
- Shared _resolve_run_context() helper for input/review commands

Usage:
    workflow status
    workflow status a1b2c3d4-e5f6-7890-abcd-ef1234567890
    workflow status --json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from cli.client import WorkflowClient, WorkflowStatusResponse
from cli.config import CLIConfig
from cli.last_run import load_last_run
from cli.main import get_console

STATUS_STYLES: dict[str, str] = {
    'COMPLETED': 'green',
    'RUNNING': 'yellow',
    'WAITING_FOR_INPUT': 'yellow',
    'WAITING_FOR_REVIEW': 'yellow',
    'FAILED': 'red',
    'PENDING': 'dim',
}


def _resolve_run_context(
    run_id_override: str | None,
    working_dir: Path,
    *,
    require_explicit: bool = False,
) -> tuple[str, str]:
    """Return (workflow_id, run_id) from .last_run or explicit override.

    Normal mode (status/input): load .last_run. If exists, use its workflow_id;
    use run_id_override if provided, otherwise use last_run.run_id. If no
    .last_run and no override: raise ValueError.

    Explicit mode (review): both --run-id AND .last_run are required because
    the API needs workflow_id from .last_run.

    Args:
        run_id_override: Explicit run ID from CLI argument, or None.
        working_dir: Directory to look for .last_run file.
        require_explicit: If True, --run-id is mandatory.

    Returns:
        Tuple of (workflow_id, run_id) as strings.

    Raises:
        ValueError: If run context cannot be resolved.
    """
    last_run = load_last_run(working_dir)

    if require_explicit:
        if run_id_override is None:
            raise ValueError('--run-id is required for this command.')
        if last_run is None:
            raise ValueError('No .last_run found. Provide --run-id and workflow ID.')
        return str(last_run.workflow_id), run_id_override

    if last_run is not None:
        wf_id = str(last_run.workflow_id)
        rid = run_id_override if run_id_override else last_run.run_id
        return wf_id, rid

    if run_id_override is not None:
        raise ValueError('--run-id provided but no .last_run found (need workflow_id).')

    raise ValueError('No .last_run found. Use --run-id.')


def _derive_node_status(
    node_id: str,
    execution_history: list[str],
    current_node_id: str | None,
    overall_status: str,
    node_outputs: dict[str, Any],
    waiting_input_node_id: str | None,
    review_node_id: str | None,
) -> str:
    """Derive the display status for a single node.

    Logic priority:
    1. Matches waiting-for-input indicator -> WAITING_FOR_INPUT
    2. Matches waiting-for-review indicator -> WAITING_FOR_REVIEW
    3. Is current node and workflow is RUNNING -> RUNNING
    4. In node_outputs or execution_history -> COMPLETED
    5. Otherwise -> PENDING

    Args:
        node_id: The node ID to derive status for.
        execution_history: List of executed node IDs from state.
        current_node_id: Currently executing node ID from state.
        overall_status: Overall workflow status string.
        node_outputs: Dict of outputs keyed by node ID.
        waiting_input_node_id: Node ID waiting for input, if any.
        review_node_id: Node ID waiting for review, if any.

    Returns:
        Status string for display.
    """
    if node_id == waiting_input_node_id:
        return 'WAITING_FOR_INPUT'
    if node_id == review_node_id:
        return 'WAITING_FOR_REVIEW'
    if node_id == current_node_id and overall_status.upper() == 'RUNNING':
        return 'RUNNING'
    if node_id in node_outputs:
        return 'COMPLETED'
    if node_id in execution_history:
        return 'COMPLETED'
    return 'PENDING'


def _format_node_id(node_id: str) -> str:
    """Format a node ID for display (first 8 chars + ellipsis).

    Args:
        node_id: Full node ID string.

    Returns:
        Truncated display string.
    """
    if len(node_id) > 8:
        return node_id[:8] + '...'
    return node_id


def _format_status_table(
    status_resp: WorkflowStatusResponse,
    nodes: list[Any],
    output_console: Console | None = None,
) -> None:
    """Print a Rich Table with summary header and color-coded per-node statuses.

    Displays a summary header with overall workflow status and node completion
    progress, followed by a Rich Table with columns: Node, Type, Status.
    Status cells are color-coded per STATUS_STYLES.

    Args:
        status_resp: Workflow status response from the API.
        nodes: List of LogicalNodePublic from list_nodes().
        output_console: Optional Rich Console override (for --no-color).
    """
    out = output_console or get_console()

    state = status_resp.state
    execution_history: list[str] = state.get('execution_history', [])
    node_outputs: dict[str, Any] = state.get('node_outputs', {})
    current_node_id: str | None = state.get('current_node_id')
    overall_status: str = state.get('execution_status', status_resp.status)

    # Extract waiting-state node IDs from state dict
    waiting_input_node_id: str | None = state.get('waiting_input_node_id')
    review_node_id: str | None = state.get('review_node_id')

    # Build node-to-type map and compute all node statuses in a single pass
    node_type_map: dict[str, str] = {}
    node_statuses: dict[str, str] = {}
    for node in nodes:
        nid = str(node.id)
        node_type_map[nid] = (
            node.config_type.value if hasattr(node.config_type, 'value') else str(node.config_type)
        )
        node_statuses[nid] = _derive_node_status(
            nid,
            execution_history,
            current_node_id,
            overall_status,
            node_outputs,
            waiting_input_node_id,
            review_node_id,
        )

    # Summary header
    completed_count = sum(1 for s in node_statuses.values() if s == 'COMPLETED')
    total = len(nodes)
    out.print(f'Workflow: {overall_status.lower()} \u2014 {completed_count}/{total} nodes complete')
    out.print(f'[dim]ID: {status_resp.workflow_id}  Run: {status_resp.run_id}[/dim]')
    out.print()

    # Build Rich Table
    table = Table(show_header=True, header_style='bold')
    table.add_column('Node', style='cyan', no_wrap=True)
    table.add_column('Type')
    table.add_column('Status')

    for node in nodes:
        nid = str(node.id)
        config_type = node_type_map.get(nid, 'UNKNOWN')
        derived = node_statuses[nid]
        color = STATUS_STYLES.get(derived, 'white')
        table.add_row(
            _format_node_id(nid),
            config_type,
            f'[{color}]{derived}[/{color}]',
        )

    out.print(table)


def _print_hints(
    status_resp: WorkflowStatusResponse,
    nodes: list[Any],
    output_console: Console | None = None,
) -> None:
    """Print actionable hints for paused nodes after the status table.

    Args:
        status_resp: Workflow status response from the API.
        nodes: List of LogicalNodePublic from list_nodes().
        output_console: Optional Rich Console override (for --no-color).
    """
    out = output_console or get_console()

    state = status_resp.state
    execution_history: list[str] = state.get('execution_history', [])
    node_outputs: dict[str, Any] = state.get('node_outputs', {})
    current_node_id: str | None = state.get('current_node_id')
    overall_status: str = state.get('execution_status', status_resp.status)
    waiting_input_node_id: str | None = state.get('waiting_input_node_id')
    review_node_id: str | None = state.get('review_node_id')

    # Build node-to-type map
    node_type_map: dict[str, str] = {}
    for node in nodes:
        nid = str(node.id)
        node_type_map[nid] = (
            node.config_type.value if hasattr(node.config_type, 'value') else str(node.config_type)
        )

    hints_printed = False
    for node in nodes:
        nid = str(node.id)
        derived = _derive_node_status(
            nid,
            execution_history,
            current_node_id,
            overall_status,
            node_outputs,
            waiting_input_node_id,
            review_node_id,
        )
        config_type = node_type_map.get(nid, 'UNKNOWN')

        if derived == 'WAITING_FOR_INPUT':
            if not hints_printed:
                out.print()
                hints_printed = True
            out.print(f"Tip: workflow input --node-id {nid} --data '{{...}}' ({config_type})")
        elif derived == 'WAITING_FOR_REVIEW':
            if not hints_printed:
                out.print()
                hints_printed = True
            out.print(
                f'Tip: workflow review --run-id {status_resp.run_id} --node-id {nid} --approve'
            )


def status_command(
    config: CLIConfig,
    run_id: str | None = None,
    json_output: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Check workflow execution status with node-by-node detail.

    Resolves .last_run context or uses an explicit run-id, calls the
    status and list_nodes APIs, and prints a unified table with overall
    state and per-node rows. Paused nodes are visually marked with
    actionable hints.

    Args:
        config: CLI configuration with API credentials.
        run_id: Optional explicit run ID (overrides .last_run).
        json_output: If True, print raw JSON and return.
        working_dir: Directory for .last_run lookup (defaults to cwd).
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()

    workflow_id, resolved_run_id = _resolve_run_context(run_id, cwd)

    with WorkflowClient.from_config(config) as client:
        status_resp = client.get_workflow_status(workflow_id, resolved_run_id)
        nodes = client.list_nodes(workflow_id)

    if json_output:
        print(json.dumps(status_resp.model_dump(), indent=2, default=str))
        return

    out = get_console()
    _format_status_table(status_resp, nodes, output_console=out)
    _print_hints(status_resp, nodes, output_console=out)
