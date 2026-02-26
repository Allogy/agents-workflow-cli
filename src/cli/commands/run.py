"""workflow run command implementation.

Starts a workflow execution via Temporal and displays progress.

Features:
- Execute by UUID or name (lockfile + API resolution)
- --input flag for initial data (JSON string or @filepath)
- Polling mode (default): poll status every 2s
- SSE streaming mode (--stream): real-time event display
- Fire-and-forget mode (--no-follow): start and exit
- .last_run context file for subsequent HITL commands
- HITL gate detection with next-step hints

Usage:
    workflow run <uuid-or-name>
    workflow run <uuid-or-name> --input '{"question": "What is AI?"}'
    workflow run <uuid-or-name> --input @input.json --stream
    workflow run <uuid-or-name> --no-follow
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid as uuid_mod
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.last_run import LastRunContext, save_last_run
from cli.lockfile import load_lockfile
from cli.sse import SSEEvent, parse_sse_line

console = Console()

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _is_uuid(value: str) -> bool:
    """Check whether a string looks like a UUID."""
    return bool(_UUID_RE.match(value))


def parse_input_arg(value: str | None) -> dict[str, Any]:
    """Parse the --input argument into a dict.

    Supports:
    - None -> empty dict
    - JSON string -> parsed dict
    - @filepath -> read file, parse as JSON

    Raises:
        FileNotFoundError: If @filepath doesn't exist.
        ValueError: If JSON is invalid.
    """
    if value is None:
        return {}

    if value.startswith('@'):
        file_path = Path(value[1:])
        if not file_path.exists():
            raise FileNotFoundError(f'Input file not found: {file_path}')
        result = json.loads(file_path.read_text())
        if not isinstance(result, dict):
            raise ValueError('Invalid JSON input: expected an object, got ' + type(result).__name__)
        return result

    try:
        result = json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f'Invalid JSON input: {e}') from e

    if not isinstance(result, dict):
        raise ValueError('Invalid JSON input: expected an object, got ' + type(result).__name__)

    return result


def resolve_workflow_id(
    identifier: str,
    client: WorkflowClient,
    org_id: str | None,
    *,
    search_dir: Path | None = None,
) -> str:
    """Resolve a workflow identifier (UUID or name) to a UUID string.

    Resolution order:
    1. UUID passthrough — if it matches UUID format, use directly.
    2. Lockfile lookup — scan *.workflow.lock in search_dir for matching name.
    3. API name search — list workflows and filter by name.

    Args:
        identifier: UUID string or workflow name.
        client: WorkflowClient for API lookups.
        org_id: Organization ID for API filtering.
        search_dir: Directory to scan for lockfiles (defaults to cwd).

    Returns:
        Workflow UUID string.

    Raises:
        ValueError: If the workflow cannot be found.
    """
    # 1. UUID passthrough
    if _is_uuid(identifier):
        return identifier

    # 2. Lockfile lookup
    dir_to_scan = search_dir or Path.cwd()
    for lock_path in dir_to_scan.glob('*.workflow.lock'):
        lock = load_lockfile(lock_path)
        if lock is None:
            continue
        # Check if the corresponding YAML has a matching name.
        # Use raw YAML parsing (not full WDF validation) so we can match
        # by name even if the file has other schema issues.
        yaml_path = lock_path.with_suffix('.yaml')
        if yaml_path.exists():
            try:
                data = yaml.safe_load(yaml_path.read_text())
                wf_name = data.get('name') if isinstance(data, dict) else None
                if wf_name and wf_name.lower() == identifier.lower():
                    return str(lock.workflow_id)
            except Exception:
                continue

    # 3. API name search
    workflows = client.list_workflows(organization_id=org_id)
    for workflow in workflows:
        try:
            metadata = client.get_metadata(workflow.id)
            if metadata.name and metadata.name.lower() == identifier.lower():
                return str(workflow.id)
        except Exception:
            continue

    raise ValueError(
        f'Workflow "{identifier}" not found. Use a UUID or ensure the name matches exactly.'
    )


# Terminal statuses — stop polling when any of these is reached
_TERMINAL_STATUSES = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}
_HITL_STATUSES = {'WAITING_FOR_REVIEW', 'WAITING_FOR_INPUT'}


def run_polling(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    *,
    total_nodes: int = 0,
    poll_interval: float = 2.0,
) -> str:
    """Poll workflow status until terminal or HITL gate.

    Args:
        client: WorkflowClient instance.
        workflow_id: UUID of the workflow.
        run_id: Run ID to poll.
        total_nodes: Total number of nodes for progress display.
        poll_interval: Seconds between polls (0 for tests).

    Returns:
        Final status string (COMPLETED, FAILED, WAITING_FOR_REVIEW, etc.).
    """
    seen_nodes: list[str] = []

    while True:
        status_resp = client.get_workflow_status(workflow_id, run_id)
        status = status_resp.status

        current_node = status_resp.current_node
        if current_node and current_node not in seen_nodes:
            seen_nodes.append(current_node)

        if current_node:
            step = len(seen_nodes)
            if total_nodes > 0:
                console.print(
                    f'  [dim][{step}/{total_nodes}][/dim] {current_node} ... {status.lower()}'
                )
            else:
                console.print(f'  [dim]{current_node}[/dim] ... {status.lower()}')

        if status in _TERMINAL_STATUSES or status in _HITL_STATUSES:
            return status

        if poll_interval > 0:
            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# SSE streaming execution
# ---------------------------------------------------------------------------

_SSE_TERMINAL_EVENTS = {'RUN_FINISHED', 'RUN_ERROR'}
_SSE_HITL_EVENTS = {'WAITING_FOR_REVIEW', 'WAITING_FOR_INPUT'}


def format_sse_event(event: SSEEvent, *, step: int = 0, total_nodes: int = 0) -> str:
    """Format an SSE event for console display.

    Args:
        event: Parsed SSE event.
        step: Current step number (1-based) for progress display.
        total_nodes: Total number of nodes for progress display.

    Returns:
        Formatted string for Rich console output.
    """
    t = event.event_type
    node_id = event.data.get('node_id', '')
    step_type = event.data.get('step_type', '')
    progress = f'[dim][{step}/{total_nodes}][/dim] ' if step > 0 and total_nodes > 0 else ''

    if t == 'RUN_STARTED':
        return '[green]▶ RUN_STARTED[/green]'
    if t == 'STEP_STARTED':
        suffix = f' ({step_type})' if step_type else ''
        return f'[blue]⏳ STEP_STARTED[/blue]  {progress}{node_id}{suffix}'
    if t == 'STEP_FINISHED':
        return f'[green]✓ STEP_FINISHED[/green] {progress}{node_id}'
    if t == 'STEP_ERROR':
        error = event.data.get('error', 'unknown error')
        return f'[red]✗ STEP_ERROR[/red]  {progress}{node_id}: {error}'
    if t == 'WAITING_FOR_REVIEW':
        return f'[yellow]⏸ WAITING_FOR_REVIEW[/yellow] at {node_id}'
    if t == 'WAITING_FOR_INPUT':
        return f'[yellow]⏸ WAITING_FOR_INPUT[/yellow] at {node_id}'
    if t == 'RUN_FINISHED':
        return '[green]✓ RUN_FINISHED[/green]'
    if t == 'RUN_ERROR':
        error = event.data.get('error', 'unknown error')
        return f'[red]✗ RUN_ERROR[/red]: {error}'
    if t == 'REVIEW_COMPLETE':
        return f'[green]✓ REVIEW_COMPLETE[/green]  {node_id}'

    return f'[dim]{t}[/dim]  {node_id}'


def run_streaming(lines: Iterator[str], *, total_nodes: int = 0) -> str:
    """Process SSE event lines until terminal or HITL event.

    Args:
        lines: Iterator of raw SSE lines (from httpx iter_lines or test data).
        total_nodes: Total number of nodes for progress display.

    Returns:
        Final event type string.
    """
    last_event_type = 'UNKNOWN'
    seen_nodes: list[str] = []

    for line in lines:
        event = parse_sse_line(line)
        if event is None:
            continue

        node_id = event.data.get('node_id', '')
        if node_id and event.event_type == 'STEP_STARTED' and node_id not in seen_nodes:
            seen_nodes.append(node_id)

        step = len(seen_nodes)
        console.print(format_sse_event(event, step=step, total_nodes=total_nodes))
        last_event_type = event.event_type

        if last_event_type in _SSE_TERMINAL_EVENTS or last_event_type in _SSE_HITL_EVENTS:
            return last_event_type

    return last_event_type


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_command(
    config: CLIConfig,
    identifier: str,
    input_data: str | None,
    *,
    stream: bool = False,
    no_follow: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Main entry point for the run command.

    Args:
        config: CLI configuration with API credentials.
        identifier: Workflow UUID or name.
        input_data: JSON string or @filepath for initial inputs.
        stream: Use SSE streaming instead of polling.
        no_follow: Fire-and-forget mode.
        working_dir: Directory for .last_run file (defaults to cwd).
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()

    # Parse input
    inputs = parse_input_arg(input_data)

    with WorkflowClient.from_config(config) as client:
        # Resolve identifier to UUID
        workflow_id = resolve_workflow_id(identifier, client, config.org_id, search_dir=cwd)

        console.print(f'[bold cyan]Running workflow:[/bold cyan] {workflow_id}')

        # Fetch node count for progress display
        try:
            nodes = client.list_nodes(workflow_id)
            total_nodes = len(nodes)
        except Exception:
            total_nodes = 0

        if stream:
            # SSE streaming mode
            run_id = str(uuid_mod.uuid4())
            console.print(f'[dim]Run ID: {run_id}[/dim]')
            console.print('[dim]Mode: SSE streaming[/dim]')
            console.print()

            # Write .last_run before starting
            ctx = LastRunContext(
                workflow_id=workflow_id,
                run_id=run_id,
                instance=config.host or '',
                started_at=datetime.now(UTC),
            )
            save_last_run(cwd, ctx)

            with client.stream_workflow_temporal(
                workflow_id, run_id=run_id, inputs=inputs
            ) as response:
                final_status = run_streaming(response.iter_lines(), total_nodes=total_nodes)

        else:
            # Start workflow (polling or no-follow)
            start_resp = client.start_workflow_temporal(workflow_id, inputs=inputs)
            run_id = start_resp.run_id

            console.print(f'[dim]Run ID: {run_id}[/dim]')

            # Write .last_run
            ctx = LastRunContext(
                workflow_id=workflow_id,
                run_id=run_id,
                instance=config.host or '',
                started_at=datetime.now(UTC),
            )
            save_last_run(cwd, ctx)

            if no_follow:
                console.print(f'[green]Workflow started.[/green] Run ID: {run_id}')
                console.print(f'[dim]Check status: workflow status {run_id}[/dim]')
                return

            # Polling mode
            console.print('[dim]Mode: polling (2s interval)[/dim]')
            console.print()
            final_status = run_polling(client, workflow_id, run_id, total_nodes=total_nodes)

        # Handle final status
        _print_final_status(final_status, run_id)

        # Exit with code 1 for failure statuses
        if final_status in _FAILURE_STATUSES:
            sys.exit(1)


# Statuses that indicate workflow failure (exit code 1)
_FAILURE_STATUSES = {'FAILED', 'RUN_ERROR', 'CANCELLED', 'TIMED_OUT'}


def _print_final_status(status: str, run_id: str) -> None:
    """Print final status message with appropriate hints."""
    if status in ('COMPLETED', 'RUN_FINISHED'):
        console.print()
        console.print('[bold green]✓ Workflow completed[/bold green]')
    elif status in ('FAILED', 'RUN_ERROR'):
        console.print()
        console.print('[bold red]✗ Workflow failed[/bold red]')
    elif status in ('CANCELLED', 'TIMED_OUT'):
        console.print()
        console.print(f'[bold red]✗ Workflow {status.lower().replace("_", " ")}[/bold red]')
    elif status == 'WAITING_FOR_REVIEW':
        console.print()
        console.print('[bold yellow]⏸  Workflow paused — waiting for human review[/bold yellow]')
        console.print(f'   Use: [cyan]workflow review {run_id} --approve[/cyan]')
    elif status == 'WAITING_FOR_INPUT':
        console.print()
        console.print('[bold yellow]⏸  Workflow paused — waiting for input[/bold yellow]')
        console.print(f"   Use: [cyan]workflow input {run_id} --data '{{...}}'[/cyan]")
    else:
        console.print(f'[dim]Final status: {status}[/dim]')
