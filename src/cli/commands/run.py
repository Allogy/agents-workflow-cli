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

import difflib
import json
import json as _json
import os
import re
import sys
import time
import uuid as uuid_mod
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console

from cli.client import WorkflowClient
from cli.config import CLIConfig, get_run_timeout
from cli.console import get_console
from cli.last_run import LastRunContext, save_last_run
from cli.lockfile import load_lockfile
from cli.sse import SSEEvent, parse_sse_line

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _is_uuid(value: str) -> bool:
    """Check whether a string looks like a UUID."""
    return bool(_UUID_RE.match(value))


def _suggest_names(target: str, available: list[str], max_suggestions: int = 3) -> list[str]:
    """Return up to max_suggestions close matches for target from available names.

    Preserves original casing in returned suggestions even though matching is
    case-insensitive.
    """
    lower_to_original = {name.lower(): name for name in available}
    matches = difflib.get_close_matches(
        target.lower(),
        list(lower_to_original.keys()),
        n=max_suggestions,
        cutoff=0.4,
    )
    return [lower_to_original[m] for m in matches]


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
        raise ValueError(
            f'Invalid JSON input: {e}\n'
            f'  Usage: --input \'{{"key": "value"}}\' or --input @file.json'
        ) from e

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

    # 3. API name search -- collect all names for suggestions
    workflows = client.list_workflows(organization_id=org_id)
    all_names: list[str] = []
    name_to_id: dict[str, str] = {}
    for workflow in workflows:
        try:
            metadata = client.get_metadata(workflow.id)
            if metadata.name:
                all_names.append(metadata.name)
                name_to_id[metadata.name.lower()] = str(workflow.id)
        except Exception:
            continue

    # Check for exact match (case-insensitive)
    match_id = name_to_id.get(identifier.lower())
    if match_id:
        return match_id

    # No exact match -- suggest close matches
    suggestions = _suggest_names(identifier, all_names)
    if suggestions:
        suggestion_list = '\n'.join(f'  - {name}' for name in suggestions)
        raise ValueError(
            f"No workflow found matching '{identifier}'. Did you mean:\n{suggestion_list}"
        )

    raise ValueError(f"No workflow found matching '{identifier}'.")


# Terminal statuses — stop polling when any of these is reached
_TERMINAL_STATUSES = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT'}
_HITL_STATUSES = {'WAITING_FOR_REVIEW', 'WAITING_FOR_INPUT'}


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-friendly duration string.

    Examples:
        1800 -> "30m"
        90 -> "1m 30s"
        45 -> "45s"
        3661 -> "1h 1m 1s"
    """
    if seconds <= 0:
        return '0s'

    parts: list[str] = []
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        parts.append(f'{hours}h')
    if minutes > 0:
        parts.append(f'{minutes}m')
    if secs > 0 or not parts:
        parts.append(f'{secs}s')

    return ' '.join(parts)


def _poll_with_retry(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    output_console: Console | None = None,
) -> Any:
    """Poll status with exponential backoff on network errors.

    Args:
        client: WorkflowClient instance.
        workflow_id: UUID of the workflow.
        run_id: Run ID to poll.
        max_retries: Maximum retry attempts on network errors.
        base_delay: Base delay in seconds (doubles each retry: 1s, 2s, 4s).
        output_console: Console to use for output (for --no-color support).

    Returns:
        WorkflowStatusResponse from the server.

    Raises:
        httpx.ConnectError: If all retries are exhausted.
        httpx.TimeoutException: If all retries are exhausted.
        httpx.ReadError: If all retries are exhausted.
    """
    out = output_console or get_console()
    for attempt in range(max_retries + 1):
        try:
            return client.get_workflow_status(workflow_id, run_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError):
            if attempt == max_retries:
                raise
            delay = base_delay * (2**attempt)  # 1s, 2s, 4s
            out.print(
                f'[dim]Network error, retrying in {delay:.0f}s... '
                f'({attempt + 1}/{max_retries})[/dim]'
            )
            time.sleep(delay)
    raise RuntimeError('Unreachable')  # pragma: no cover


def run_polling(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    *,
    total_nodes: int = 0,
    poll_interval: float = 2.0,
    max_timeout_seconds: int | float | None = None,
    output_console: Console | None = None,
) -> str:
    """Poll workflow status until terminal or HITL gate.

    Args:
        client: WorkflowClient instance.
        workflow_id: UUID of the workflow.
        run_id: Run ID to poll.
        total_nodes: Total number of nodes for progress display.
        poll_interval: Seconds between polls (0 for tests).
        max_timeout_seconds: Maximum wall-clock time before timeout.
            Defaults to the value from get_run_timeout() (30 minutes).
        output_console: Console to use for output (for --no-color support).

    Returns:
        Final status string (COMPLETED, FAILED, WAITING_FOR_REVIEW, etc.).

    Raises:
        SystemExit: With code 1 if timeout is reached.
    """
    out = output_console or get_console()
    timeout = max_timeout_seconds if max_timeout_seconds is not None else get_run_timeout()
    start_time = time.monotonic()
    seen_nodes: list[str] = []

    while True:
        status_resp = _poll_with_retry(client, workflow_id, run_id, output_console=output_console)
        status = status_resp.status

        current_node = status_resp.current_node
        if current_node and current_node not in seen_nodes:
            seen_nodes.append(current_node)

        if current_node:
            step = len(seen_nodes)
            if total_nodes > 0:
                out.print(
                    f'  [dim][{step}/{total_nodes}][/dim] {current_node} ... {status.lower()}'
                )
            else:
                out.print(f'  [dim]{current_node}[/dim] ... {status.lower()}')

        # Normalize to uppercase for comparison (backend returns mixed case)
        status_upper = status.upper()
        if status_upper in _TERMINAL_STATUSES or status_upper in _HITL_STATUSES:
            return status

        if poll_interval > 0:
            time.sleep(poll_interval)

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            out.print(
                f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# SSE streaming execution
# ---------------------------------------------------------------------------

_SSE_TERMINAL_EVENTS = {'RUN_FINISHED', 'RUN_ERROR'}
_SSE_HITL_EVENTS = {'WAITING_FOR_REVIEW', 'WAITING_FOR_INPUT'}

# Color mapping by event type category
_SSE_COLOR_MAP: dict[str, str] = {
    'RUN_STARTED': 'green',
    'STEP_FINISHED': 'green',
    'RUN_FINISHED': 'green',
    'REVIEW_COMPLETE': 'green',
    'STEP_STARTED': 'blue',
    'WAITING_FOR_REVIEW': 'yellow',
    'WAITING_FOR_INPUT': 'yellow',
    'STEP_ERROR': 'red',
    'RUN_ERROR': 'red',
}


def format_sse_compact(event: SSEEvent, *, step: int = 0, total_nodes: int = 0) -> str:
    """Format an SSE event in compact one-line format with timestamp.

    Format: [HH:MM:SS] EVENT_TYPE node-name
    Color-coded by event type category.

    Args:
        event: Parsed SSE event.
        step: Current step number (1-based) for progress display.
        total_nodes: Total number of nodes for progress display.

    Returns:
        Formatted string for Rich console output.
    """
    ts = datetime.now().strftime('%H:%M:%S')
    t = event.event_type
    node_id = event.data.get('node_id', '')
    color = _SSE_COLOR_MAP.get(t, 'dim')
    suffix = f' {node_id}' if node_id else ''

    # Add error detail for error events
    if t in ('STEP_ERROR', 'RUN_ERROR'):
        error = event.data.get('error', '')
        if error:
            suffix += f': {error}'

    return f'[dim][{ts}][/dim] [{color}]{t}[/{color}]{suffix}'


def format_sse_verbose(event: SSEEvent, *, step: int = 0, total_nodes: int = 0) -> str:
    """Format an SSE event in verbose multi-line format with payload excerpts.

    Shows timestamp, event type, node info, and a payload excerpt.

    Args:
        event: Parsed SSE event.
        step: Current step number (1-based) for progress display.
        total_nodes: Total number of nodes for progress display.

    Returns:
        Formatted string for Rich console output.
    """
    ts = datetime.now().strftime('%H:%M:%S')
    t = event.event_type
    node_id = event.data.get('node_id', '')
    color = _SSE_COLOR_MAP.get(t, 'dim')

    lines = [f'[dim][{ts}][/dim] [{color}]{t}[/{color}]']
    if node_id:
        lines[0] += f'  {node_id}'

    # Add step type info
    step_type = event.data.get('step_type', '')
    if step_type:
        lines.append(f'  [dim]Type:[/dim] {step_type}')

    # Add payload excerpt (exclude type and node_id which are already shown)
    payload_keys = {
        k: v for k, v in event.data.items() if k not in ('type', 'node_id', 'step_type')
    }
    if payload_keys:
        excerpt = _json.dumps(payload_keys, indent=2, default=str)
        # Truncate long payloads
        max_lines = 6
        excerpt_lines = excerpt.split('\n')
        if len(excerpt_lines) > max_lines:
            excerpt = '\n'.join(excerpt_lines[:max_lines]) + '\n  ...'
        lines.append(f'  [dim]Payload:[/dim]\n  {excerpt}')

    return '\n'.join(lines)


# Backward compatibility alias
format_sse_event = format_sse_compact


def run_streaming(
    lines: Iterator[str],
    *,
    total_nodes: int = 0,
    max_timeout_seconds: int | float | None = None,
    verbose: bool = False,
    output_console: Console | None = None,
) -> str:
    """Process SSE event lines until terminal or HITL event.

    Args:
        lines: Iterator of raw SSE lines (from httpx iter_lines or test data).
        total_nodes: Total number of nodes for progress display.
        max_timeout_seconds: Maximum wall-clock time before timeout.
            Defaults to the value from get_run_timeout() (30 minutes).
        verbose: Use verbose multi-line format instead of compact.
        output_console: Console to use for output (for --no-color support).

    Returns:
        Final event type string.

    Raises:
        SystemExit: With code 1 if timeout is reached.
    """
    timeout = max_timeout_seconds if max_timeout_seconds is not None else get_run_timeout()
    start_time = time.monotonic()
    last_event_type = 'UNKNOWN'
    seen_nodes: list[str] = []
    fmt = format_sse_verbose if verbose else format_sse_compact
    out = output_console or get_console()

    for line in lines:
        event = parse_sse_line(line)
        if event is None:
            continue

        node_id = event.data.get('node_id', '')
        if node_id and event.event_type == 'STEP_STARTED' and node_id not in seen_nodes:
            seen_nodes.append(node_id)

        step = len(seen_nodes)
        out.print(fmt(event, step=step, total_nodes=total_nodes))
        last_event_type = event.event_type

        # Normalize to uppercase for comparison (backend returns mixed case)
        if last_event_type.upper() in _SSE_HITL_EVENTS:
            # Print actionable HITL hint
            hitl_node_id = event.data.get('node_id', '<node-id>')
            if last_event_type.upper() == 'WAITING_FOR_INPUT':
                out.print(
                    f"  [dim]Next: workflow input --node-id {hitl_node_id} --data '{{...}}'[/dim]"
                )
            elif last_event_type.upper() == 'WAITING_FOR_REVIEW':
                out.print('  [dim]Next: workflow review --approve[/dim]')
            return last_event_type

        if last_event_type.upper() in _SSE_TERMINAL_EVENTS:
            return last_event_type

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            out.print(
                f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
            )
            sys.exit(1)

    # Stream ended without terminal event -- connection may have been interrupted
    if last_event_type.upper() not in _SSE_TERMINAL_EVENTS and last_event_type.upper() not in (
        _SSE_HITL_EVENTS
    ):
        out.print(
            '[bold yellow]Warning:[/bold yellow] Stream interrupted before completion. '
            'Use [cyan]workflow status[/cyan] to check current state.'
        )

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
    verbose: bool = False,
    no_color: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Main entry point for the run command.

    Args:
        config: CLI configuration with API credentials.
        identifier: Workflow UUID or name.
        input_data: JSON string or @filepath for initial inputs.
        stream: Use SSE streaming instead of polling.
        no_follow: Fire-and-forget mode.
        verbose: Use verbose multi-line SSE output.
        no_color: Disable colored output.
        working_dir: Directory for .last_run file (defaults to cwd).
    """
    config.validate_for_api()
    cwd = working_dir or Path.cwd()

    # Per-command --no-color overrides; otherwise get_console() respects the global flag.
    # Rich Console natively honours the NO_COLOR env var.
    output_console: Console | None = None
    if no_color or os.environ.get('NO_COLOR'):
        output_console = Console(no_color=True)
    else:
        output_console = get_console()

    # Parse input
    inputs = parse_input_arg(input_data)

    with WorkflowClient.from_config(config) as client:
        # Resolve identifier to UUID
        workflow_id = resolve_workflow_id(identifier, client, config.org_id, search_dir=cwd)

        output_console.print(f'[bold cyan]Running workflow:[/bold cyan] {workflow_id}')

        # Fetch node count for progress display
        try:
            nodes = client.list_nodes(workflow_id)
            total_nodes = len(nodes)
        except Exception:
            total_nodes = 0

        if stream:
            # SSE streaming mode
            run_id = str(uuid_mod.uuid4())
            output_console.print(f'[dim]Run ID: {run_id}[/dim]')
            output_console.print('[dim]Mode: SSE streaming[/dim]')
            output_console.print()

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
                final_status = run_streaming(
                    response.iter_lines(),
                    total_nodes=total_nodes,
                    verbose=verbose,
                    output_console=output_console,
                )

        else:
            # Start workflow (polling or no-follow)
            start_resp = client.start_workflow_temporal(workflow_id, inputs=inputs)
            run_id = start_resp.run_id

            output_console.print(f'[dim]Run ID: {run_id}[/dim]')

            # Write .last_run
            ctx = LastRunContext(
                workflow_id=workflow_id,
                run_id=run_id,
                instance=config.host or '',
                started_at=datetime.now(UTC),
            )
            save_last_run(cwd, ctx)

            if no_follow:
                output_console.print(f'[green]Workflow started.[/green] Run ID: {run_id}')
                output_console.print(f'[dim]Check status: workflow status {run_id}[/dim]')
                return

            # Polling mode
            output_console.print('[dim]Mode: polling (2s interval)[/dim]')
            output_console.print()
            final_status = run_polling(
                client,
                workflow_id,
                run_id,
                total_nodes=total_nodes,
                output_console=output_console,
            )

        # Handle final status
        _print_final_status(final_status, run_id, output_console=output_console)

        # Exit with code 1 for failure statuses (case-insensitive)
        if final_status.upper() in _FAILURE_STATUSES:
            sys.exit(1)


# Statuses that indicate workflow failure (exit code 1)
_FAILURE_STATUSES = {'FAILED', 'RUN_ERROR', 'CANCELLED', 'TIMED_OUT'}


def _print_final_status(
    status: str,
    run_id: str,
    output_console: Console | None = None,
) -> None:
    """Print final status message with appropriate hints."""
    out = output_console or get_console()
    status_upper = status.upper()
    if status_upper in ('COMPLETED', 'RUN_FINISHED'):
        out.print()
        out.print('[bold green]✓ Workflow completed[/bold green]')
    elif status_upper in ('FAILED', 'RUN_ERROR'):
        out.print()
        out.print('[bold red]✗ Workflow failed[/bold red]')
    elif status_upper in ('CANCELLED', 'TIMED_OUT'):
        out.print()
        out.print(f'[bold red]✗ Workflow {status.lower().replace("_", " ")}[/bold red]')
    elif status_upper == 'WAITING_FOR_REVIEW':
        out.print()
        out.print('[bold yellow]⏸  Workflow paused — waiting for human review[/bold yellow]')
        out.print(f'   Use: [cyan]workflow review {run_id} --approve[/cyan]')
    elif status_upper == 'WAITING_FOR_INPUT':
        out.print()
        out.print('[bold yellow]⏸  Workflow paused — waiting for input[/bold yellow]')
        out.print(f"   Use: [cyan]workflow input {run_id} --data '{{...}}'[/cyan]")
    else:
        out.print(f'[dim]Final status: {status}[/dim]')
