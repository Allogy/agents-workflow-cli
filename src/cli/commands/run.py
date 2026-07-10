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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from cli.client import WorkflowClient, WorkflowStatusResponse
from cli.config import CLIConfig, get_run_timeout
from cli.console import get_console
from cli.last_run import LastRunContext, save_last_run
from cli.lockfile import load_lockfile
from cli.sse import (
    HITL_EVENTS,
    TERMINAL_EVENTS,
    AGUIEventType,
    SSEEvent,
    parse_sse_line,
)

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


def parse_file_input(value: str) -> list[Path]:
    """Parse a file:// prefixed input string into a list of file paths.

    Supports:
    - Single file: 'file:///path/to/file.pdf'
    - Multiple files: 'file:///path/to/file1.pdf,file:///path/to/file2.docx'
    - Bare paths without file:// prefix

    Args:
        value: Input string with file:// prefix(es), or bare path(s).

    Returns:
        List of validated Path objects.

    Raises:
        FileNotFoundError: If any file path doesn't exist.
        ValueError: If a path is not a regular file.
    """
    parts = value.split(',')
    paths: list[Path] = []
    for part in parts:
        raw = part.strip()
        if raw.startswith('file://'):
            raw = raw[len('file://') :]
        p = Path(raw).expanduser()
        if not p.exists():
            raise FileNotFoundError(f'File not found: {p}')
        if not p.is_file():
            raise ValueError(f'Not a file: {p}')
        paths.append(p)
    return paths


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
_TERMINAL_STATUSES = {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'TIMEOUT'}
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
    quiet: bool = False,
) -> Any:
    """Poll status with exponential backoff on network errors.

    Args:
        client: WorkflowClient instance.
        workflow_id: UUID of the workflow.
        run_id: Run ID to poll.
        max_retries: Maximum retry attempts on network errors.
        base_delay: Base delay in seconds (doubles each retry: 1s, 2s, 4s).
        output_console: Console to use for output (for --no-color support).
        quiet: If True, suppress retry progress output.

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
            if not quiet:
                out.print(
                    f'[dim]Network error, retrying in {delay:.0f}s... '
                    f'({attempt + 1}/{max_retries})[/dim]'
                )
            time.sleep(delay)
    raise RuntimeError('Unreachable')  # pragma: no cover


_STREAM_RETRY_ATTEMPTS = 2
_STREAM_RETRY_DELAY_S = 1.5
_RETRYABLE_STATUS_CODES = {502, 503}


def _stream_with_retry(
    client: WorkflowClient,
    workflow_id: str,
    *,
    run_id: str,
    inputs: dict[str, Any],
    max_retries: int = _STREAM_RETRY_ATTEMPTS,
    retry_delay: float = _STREAM_RETRY_DELAY_S,
    output_console: Console | None = None,
    quiet: bool = False,
) -> Any:
    """Open SSE stream with retry on transient HTTP errors (502, 503).

    Returns:
        Context manager from client.stream_workflow_temporal.

    Raises:
        httpx.HTTPStatusError: If all retries exhausted or non-retryable error.
    """
    out = output_console or get_console()
    last_err: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return client.stream_workflow_temporal(workflow_id, run_id=run_id, inputs=inputs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _RETRYABLE_STATUS_CODES:
                raise
            last_err = e
            if attempt < max_retries:
                if not quiet:
                    out.print(
                        f'[dim]Transient error ({e.response.status_code}), '
                        f'retrying in {retry_delay:.1f}s... '
                        f'({attempt + 1}/{max_retries})[/dim]'
                    )
                time.sleep(retry_delay)
        except (httpx.ConnectError, httpx.ReadError) as e:
            last_err = e
            if attempt < max_retries:
                if not quiet:
                    out.print(
                        f'[dim]Network error, retrying in {retry_delay:.1f}s... '
                        f'({attempt + 1}/{max_retries})[/dim]'
                    )
                time.sleep(retry_delay)
            else:
                raise

    raise last_err  # type: ignore[misc]


def run_polling(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    *,
    total_nodes: int = 0,
    poll_interval: float = 2.0,
    max_timeout_seconds: int | float | None = None,
    output_console: Console | None = None,
    pending_input: dict[str, Any] | None = None,
    json_output: bool = False,
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
        pending_input: If provided, auto-submit this data to the first
            WAITING_FOR_INPUT node and continue polling (BUG-4 fix).
        json_output: If True, suppress Rich progress output.

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
        status_resp = _poll_with_retry(
            client,
            workflow_id,
            run_id,
            output_console=output_console,
            quiet=json_output,
        )
        status = status_resp.status

        current_node = status_resp.current_node
        if current_node and current_node not in seen_nodes:
            seen_nodes.append(current_node)

        if current_node and not json_output:
            step = len(seen_nodes)
            if total_nodes > 0:
                out.print(
                    f'  [dim][{step}/{total_nodes}][/dim] {current_node} ... {status.lower()}'
                )
            else:
                out.print(f'  [dim]{current_node}[/dim] ... {status.lower()}')

        # Normalize to uppercase for comparison (backend returns mixed case)
        status_upper = status.upper()
        effective_status = status_upper

        # Defensive: check state.execution_status when top-level status is RUNNING
        # (backend may return Temporal's WorkflowExecutionStatus instead of internal state)
        if status_upper == 'RUNNING':
            exec_status = (status_resp.state or {}).get('execution_status', '').upper()
            if exec_status in _HITL_STATUSES or exec_status in _TERMINAL_STATUSES:
                effective_status = exec_status

        # Auto-submit pending input when workflow hits WAITING_FOR_INPUT (BUG-4)
        if effective_status == 'WAITING_FOR_INPUT' and pending_input is not None:
            _state = status_resp.state or {}
            waiting_node = (
                _state.get('waiting_for_input_node_id')
                or _state.get('waiting_input_node_id')
                or _state.get('current_node_id', '')
            )
            if waiting_node:
                try:
                    client.submit_input(
                        workflow_id,
                        run_id=run_id,
                        node_id=waiting_node,
                        input_data=pending_input,
                    )
                    pending_input = None  # Only auto-submit once
                    if not json_output:
                        out.print(f'[green]Auto-submitted input to {waiting_node}[/green]')
                    if poll_interval > 0:
                        time.sleep(poll_interval)
                    continue  # Resume polling
                except Exception as e:
                    pending_input = None  # Don't retry on failure
                    if not json_output:
                        out.print(f'[yellow]Auto-submit failed: {e}[/yellow]')

        if effective_status in _TERMINAL_STATUSES or effective_status in _HITL_STATUSES:
            return effective_status if effective_status != status_upper else status

        if poll_interval > 0:
            time.sleep(poll_interval)

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            if not json_output:
                out.print(
                    f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                    f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
                )
            sys.exit(1)


def _poll_until_next_event(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    *,
    poll_interval: float = 2.5,
    verbose: bool = False,
    output_console: Console | None = None,
    max_timeout_seconds: int | float | None = None,
) -> str:
    """Poll workflow status until terminal or next HITL state.

    Unlike :func:`run_polling`, this function is designed for post-HITL-submission
    monitoring and prints status transitions in a streaming-consistent format.

    Args:
        client: WorkflowClient for API calls.
        workflow_id: UUID of the workflow.
        run_id: Run identifier.
        poll_interval: Seconds between polls (default 2.5).
        verbose: Use verbose formatter (reserved for future use).
        output_console: Console for output.
        max_timeout_seconds: Maximum wall-clock time before timeout.

    Returns:
        Final status string (e.g. ``'COMPLETED'``, ``'WAITING_FOR_INPUT'``, etc.).

    Raises:
        SystemExit: With code 1 if timeout is reached.
    """
    out = output_console or get_console()
    timeout = max_timeout_seconds if max_timeout_seconds is not None else get_run_timeout()
    start_time = time.monotonic()
    last_node = ''

    # Brief initial delay to let backend process the HITL submission signal
    # (Temporal signal processing is async — Pitfall 4 from research)
    time.sleep(poll_interval * 0.5)

    while True:
        status_resp = _poll_with_retry(client, workflow_id, run_id, output_console=out)
        status_upper = status_resp.status.upper()
        current_node = status_resp.current_node or ''

        if current_node and current_node != last_node:
            ts = datetime.now().strftime('%H:%M:%S')
            out.print(f'[dim][{ts}][/dim] [blue]Processing[/blue] {current_node}')

        last_node = current_node

        # Defensive: check state.execution_status when top-level status is RUNNING
        if status_upper == 'RUNNING':
            exec_status = (status_resp.state or {}).get('execution_status', '').upper()
            if exec_status in _HITL_STATUSES or exec_status in _TERMINAL_STATUSES:
                return exec_status

        if status_upper in _TERMINAL_STATUSES or status_upper in _HITL_STATUSES:
            return status_resp.status

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            out.print(
                f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
            )
            sys.exit(1)

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# SSE streaming execution
# ---------------------------------------------------------------------------

_SSE_TERMINAL_EVENTS = {e.value for e in TERMINAL_EVENTS}
_SSE_HITL_EVENTS = {e.value for e in HITL_EVENTS} - {AGUIEventType.REVIEW_COMPLETE.value}

# Color mapping by event type category
_SSE_COLOR_MAP: dict[str, str] = {
    'RUN_STARTED': 'green',
    'STEP_FINISHED': 'green',
    'RUN_FINISHED': 'green',
    'REVIEW_COMPLETE': 'green',
    'STEP_STARTED': 'blue',
    'STATE_SNAPSHOT': 'blue',
    'STATE_DELTA': 'cyan',
    'TEXT_MESSAGE_START': 'cyan',
    'TEXT_MESSAGE_CONTENT': 'cyan',
    'TEXT_MESSAGE_END': 'cyan',
    'TOOL_CALL_START': 'magenta',
    'TOOL_CALL_ARGS': 'magenta',
    'TOOL_CALL_END': 'magenta',
    'TOOL_CALL_RESULT': 'magenta',
    'WAITING_FOR_REVIEW': 'yellow',
    'WAITING_FOR_INPUT': 'yellow',
    'STEP_ERROR': 'red',
    'RUN_ERROR': 'red',
    'CUSTOM': 'dim',
    'RAW': 'dim',
}

_KNOWN_EVENT_TYPES = set(_SSE_COLOR_MAP.keys())


@dataclass
class NodeResult:
    """Outcome of a single node during streaming."""

    node_id: str
    display_name: str
    step_type: str
    status: str  # 'started', 'finished', 'error'
    duration_ms: int | None = None


@dataclass
class StreamResult:
    """Complete result of a streaming run."""

    final_event: str
    nodes: list[NodeResult] = field(default_factory=list)


def _get_display_name(event: SSEEvent) -> str:
    """Get human-readable node display name, preferring slug over UUID."""
    return (
        event.data.get('node_slug') or event.data.get('step_name') or event.data.get('node_id', '')
    )


def _format_duration_ms(ms: int) -> str:
    """Format milliseconds into a human-friendly duration string."""
    if ms < 1000:
        return f'{ms}ms'
    return _format_duration(ms // 1000)


def format_unknown_event(event: SSEEvent, *, max_chars: int = 100) -> str:
    """Format an unknown SSE event as dim text with truncated payload."""
    ts = datetime.now().strftime('%H:%M:%S')
    raw = json.dumps(event.data, default=str)
    if len(raw) > max_chars:
        raw = raw[:max_chars] + '...'
    return f'[dim][{ts}] {event.event_type}: {raw}[/dim]'


def format_sse_compact(event: SSEEvent, *, step: int = 0, total_nodes: int = 0) -> str:
    """Format an SSE event in compact one-line format with timestamp.

    Format: [HH:MM:SS] EVENT_TYPE node-name (details)
    Color-coded by event type category. Shows step_type on STEP_STARTED,
    duration on STEP_FINISHED, and structured errors on error events.

    Args:
        event: Parsed SSE event.
        step: Current step number (1-based) for progress display.
        total_nodes: Total number of nodes for progress display.

    Returns:
        Formatted string for Rich console output.
    """
    ts = datetime.now().strftime('%H:%M:%S')
    t = event.event_type
    display_name = _get_display_name(event)
    color = _SSE_COLOR_MAP.get(t, 'dim')
    suffix = f' {display_name}' if display_name else ''

    if t == 'STEP_STARTED':
        step_type = event.data.get('step_type', '')
        if step_type:
            suffix += f' ({step_type})'
    elif t == 'STEP_FINISHED':
        duration_ms = event.data.get('duration_ms')
        if duration_ms is not None:
            suffix += f' [dim]{_format_duration_ms(duration_ms)}[/dim]'
    elif t == 'STATE_DELTA':
        delta = event.data.get('delta', [])
        for op in delta:
            path = op.get('path', '')
            value = op.get('value')
            if '/node_outputs/' in path and isinstance(value, dict):
                text = value.get('text') or value.get('content') or value.get('response', '')
                if text and isinstance(text, str):
                    preview = text[:200] + '...' if len(text) > 200 else text
                    suffix += f': {preview}'
    elif t == 'STATE_SNAPSHOT':
        snapshot = event.data.get('snapshot', event.data)
        node_outputs = snapshot.get('node_outputs', {})
        if node_outputs:
            suffix += f' ({len(node_outputs)} node outputs)'
    elif t == 'TEXT_MESSAGE_CONTENT':
        content = event.data.get('content', '')
        if content:
            suffix += f': {content[:200]}'
    elif t == 'TOOL_CALL_START':
        tool_name = event.data.get('name', '')
        if tool_name:
            suffix += f' ({tool_name})'
    elif t == 'CUSTOM':
        name = event.data.get('name', '')
        if name:
            suffix += f' ({name})'
    elif t in ('STEP_ERROR', 'RUN_ERROR'):
        error = event.data.get('error', '')
        if error:
            suffix += f': {error}'
        error_type = event.data.get('error_type', '')
        if error_type:
            suffix += f' [dim]({error_type})[/dim]'
        code = event.data.get('code', '')
        if code:
            suffix += f' [dim][{code}][/dim]'

    return f'[dim][{ts}][/dim] [{color}]{t}[/{color}]{suffix}'


def format_sse_verbose(event: SSEEvent, *, step: int = 0, total_nodes: int = 0) -> str:
    """Format an SSE event in verbose multi-line format with payload excerpts.

    Shows timestamp, event type, node info, and a payload excerpt.
    Error events are expanded to multi-line with error, error_type, and code.
    Unknown events show the full raw payload.

    Args:
        event: Parsed SSE event.
        step: Current step number (1-based) for progress display.
        total_nodes: Total number of nodes for progress display.

    Returns:
        Formatted string for Rich console output.
    """
    ts = datetime.now().strftime('%H:%M:%S')
    t = event.event_type
    display_name = _get_display_name(event)
    color = _SSE_COLOR_MAP.get(t, 'dim')

    lines = [f'[dim][{ts}][/dim] [{color}]{t}[/{color}]']
    if display_name:
        lines[0] += f'  {display_name}'

    # Add step type info
    step_type = event.data.get('step_type', '')
    if step_type:
        lines.append(f'  [dim]Type:[/dim] {step_type}')

    # Error events: multi-line expansion
    if t in ('STEP_ERROR', 'RUN_ERROR'):
        error = event.data.get('error', '')
        if error:
            lines.append(f'  Error: {error}')
        error_type = event.data.get('error_type', '')
        if error_type:
            lines.append(f'  Type: {error_type}')
        code = event.data.get('code', '')
        if code:
            lines.append(f'  Code: {code}')
        traceback = event.data.get('traceback', '')
        if traceback:
            lines.append(f'  Traceback: {traceback}')
    elif t not in _KNOWN_EVENT_TYPES:
        # Unknown events: show full raw payload
        raw = _json.dumps(event.data, indent=2, default=str)
        lines.append(f'  [dim]Payload:[/dim]\n  {raw}')
    else:
        # Known non-error events: payload excerpt
        payload_keys = {
            k: v
            for k, v in event.data.items()
            if k not in ('type', 'node_id', 'node_slug', 'step_name', 'step_type')
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


def _print_summary_table(nodes: list[NodeResult], console: Console) -> None:
    """Print a Rich table summarizing all node outcomes."""
    if not nodes:
        return
    table = Table(title='Run Summary', show_header=True, header_style='bold')
    table.add_column('Node', style='cyan', no_wrap=True)
    table.add_column('Type', style='dim')
    table.add_column('Status', justify='center')
    table.add_column('Duration', justify='right', style='dim')

    for node in nodes:
        if node.status == 'finished':
            status_str = '[green]OK[/green]'
        elif node.status == 'error':
            status_str = '[red]ERROR[/red]'
        else:
            status_str = f'[yellow]{node.status}[/yellow]'
        duration_str = (
            _format_duration_ms(node.duration_ms) if node.duration_ms is not None else '-'
        )
        table.add_row(
            node.display_name or node.node_id, node.step_type or '-', status_str, duration_str
        )

    console.print()
    console.print(table)


def run_streaming(
    lines: Iterator[str],
    *,
    total_nodes: int = 0,
    max_timeout_seconds: int | float | None = None,
    verbose: bool = False,
    output_console: Console | None = None,
    pending_input: dict[str, Any] | None = None,
    submit_input_fn: Callable[[str, dict[str, Any]], Any] | None = None,
    json_output: bool = False,
) -> StreamResult:
    """Process SSE event lines until terminal or HITL event.

    Args:
        lines: Iterator of raw SSE lines (from httpx iter_lines or test data).
        total_nodes: Total number of nodes for progress display.
        max_timeout_seconds: Maximum wall-clock time before timeout.
            Defaults to the value from get_run_timeout() (30 minutes).
        verbose: Use verbose multi-line format instead of compact.
        output_console: Console to use for output (for --no-color support).
        pending_input: Pre-supplied input data to auto-submit on WAITING_FOR_INPUT (BUG-4).
        submit_input_fn: Callable(node_id, data) used to submit pending_input automatically.
        json_output: If True, emit raw decoded event dicts as compact NDJSON.

    Returns:
        StreamResult with final event type and accumulated node results.

    Raises:
        SystemExit: With code 1 if timeout is reached.
    """
    timeout = max_timeout_seconds if max_timeout_seconds is not None else get_run_timeout()
    start_time = time.monotonic()
    last_event_type = 'UNKNOWN'
    seen_nodes: list[str] = []
    fmt = format_sse_verbose if verbose else format_sse_compact
    out = output_console or get_console()
    node_results: dict[str, NodeResult] = {}  # keyed by node_id
    node_start_times: dict[str, float] = {}  # client-side timing fallback

    for line in lines:
        event = parse_sse_line(line)
        if event is None:
            continue

        if json_output:
            print(json.dumps(event.data, separators=(',', ':'), default=str), flush=True)

        event_type_upper = event.event_type.upper()
        node_id = event.data.get('node_id', '')

        # Handle CUSTOM SSE_PAUSING: stream is about to close for HITL
        if event_type_upper == 'CUSTOM' and event.data.get('name') == 'SSE_PAUSING':
            if not json_output:
                out.print(fmt(event, step=len(seen_nodes), total_nodes=total_nodes))
            last_event_type = event.event_type
            continue

        # Unknown event routing
        if (
            event_type_upper not in _KNOWN_EVENT_TYPES
            and event_type_upper not in _SSE_TERMINAL_EVENTS
            and event_type_upper not in _SSE_HITL_EVENTS
        ):
            if not json_output:
                out.print(format_unknown_event(event))
            last_event_type = event.event_type
            # Check timeout even for unknown events
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                if not json_output:
                    out.print(
                        f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                        f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
                    )
                sys.exit(1)
            continue

        # Node tracking: STEP_STARTED
        if event_type_upper == 'STEP_STARTED' and node_id:
            node_start_times[node_id] = time.monotonic()
            node_results[node_id] = NodeResult(
                node_id=node_id,
                display_name=_get_display_name(event),
                step_type=event.data.get('step_type', ''),
                status='started',
            )

        # Node tracking: STEP_FINISHED
        if event_type_upper == 'STEP_FINISHED' and node_id:
            duration_ms = event.data.get('duration_ms')
            if duration_ms is None and node_id in node_start_times:
                duration_ms = int((time.monotonic() - node_start_times[node_id]) * 1000)
            if node_id in node_results:
                node_results[node_id].status = 'finished'
                node_results[node_id].duration_ms = duration_ms
                # Update display_name if STEP_FINISHED carries a better slug
                finished_display = _get_display_name(event)
                if finished_display:
                    node_results[node_id].display_name = finished_display
            else:
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    display_name=_get_display_name(event),
                    step_type=event.data.get('step_type', ''),
                    status='finished',
                    duration_ms=duration_ms,
                )

        # Node tracking: STEP_ERROR
        if event_type_upper == 'STEP_ERROR' and node_id:
            duration_ms = event.data.get('duration_ms')
            if duration_ms is None and node_id in node_start_times:
                duration_ms = int((time.monotonic() - node_start_times[node_id]) * 1000)
            if node_id in node_results:
                node_results[node_id].status = 'error'
                node_results[node_id].duration_ms = duration_ms
                finished_display = _get_display_name(event)
                if finished_display:
                    node_results[node_id].display_name = finished_display
            else:
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    display_name=_get_display_name(event),
                    step_type=event.data.get('step_type', ''),
                    status='error',
                    duration_ms=duration_ms,
                )

        if node_id and event_type_upper == 'STEP_STARTED' and node_id not in seen_nodes:
            seen_nodes.append(node_id)

        step = len(seen_nodes)
        if not json_output:
            out.print(fmt(event, step=step, total_nodes=total_nodes))
        last_event_type = event.event_type

        # Normalize to uppercase for comparison (backend returns mixed case)
        if last_event_type.upper() in _SSE_HITL_EVENTS:
            # Print actionable HITL hint
            hitl_node_id = event.data.get('node_id', '<node-id>')
            if (
                not json_output
                and last_event_type.upper() == 'WAITING_FOR_INPUT'
                and not (pending_input is not None and submit_input_fn is not None)
            ):
                out.print(
                    f"  [dim]Next: workflow input --node-id {hitl_node_id} --data '{{...}}'[/dim]"
                )
            elif not json_output and last_event_type.upper() == 'WAITING_FOR_REVIEW':
                out.print('  [dim]Next: workflow review --approve[/dim]')
            # Auto-submit pending input in streaming mode (BUG-4)
            if (
                last_event_type.upper() == 'WAITING_FOR_INPUT'
                and pending_input is not None
                and submit_input_fn is not None
            ):
                try:
                    submit_input_fn(hitl_node_id, pending_input)
                    if not json_output:
                        out.print(f'[green]Auto-submitted input to {hitl_node_id}[/green]')
                    pending_input = None  # Only auto-submit once
                except Exception as e:
                    if not json_output:
                        out.print(f'[yellow]Auto-submit failed: {e}[/yellow]')
                    pending_input = None  # Don't retry on failure
            return StreamResult(final_event=last_event_type, nodes=list(node_results.values()))

        if last_event_type.upper() in _SSE_TERMINAL_EVENTS:
            if last_event_type.upper() == 'RUN_FINISHED' and not json_output:
                _print_summary_table(list(node_results.values()), out)
            return StreamResult(final_event=last_event_type, nodes=list(node_results.values()))

        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            if not json_output:
                out.print(
                    f'[bold red]Timeout after {_format_duration(int(timeout))}.[/bold red] '
                    f'Workflow may still be running. Use [cyan]workflow status[/cyan] to check.'
                )
            sys.exit(1)

    # Stream ended without terminal event -- connection may have been interrupted
    if last_event_type.upper() not in _SSE_TERMINAL_EVENTS and last_event_type.upper() not in (
        _SSE_HITL_EVENTS
    ):
        if not json_output:
            out.print(
                '[bold yellow]Warning:[/bold yellow] Stream interrupted before completion. '
                'Use [cyan]workflow status[/cyan] to check current state.'
            )

    return StreamResult(final_event=last_event_type, nodes=list(node_results.values()))


# ---------------------------------------------------------------------------
# Interactive HITL loop
# ---------------------------------------------------------------------------


def _run_interactive(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    initial_result: StreamResult,
    *,
    total_nodes: int = 0,
    verbose: bool = False,
    output_console: Console | None = None,
    node_map: dict[str, tuple[str, str]] | None = None,
) -> StreamResult:
    """Interactive HITL loop: stream -> prompt -> submit -> poll -> repeat.

    Orchestrates the full interactive session from initial SSE streaming
    through all HITL gates until the workflow reaches a terminal state.

    Args:
        client: WorkflowClient for API calls.
        workflow_id: UUID of the workflow.
        run_id: Run ID for this execution.
        initial_result: The StreamResult from the initial SSE streaming phase.
        total_nodes: Total node count for progress display.
        verbose: Use verbose formatter.
        output_console: Console for output.
        node_map: Optional pre-built ``{node_id: (slug, step_type)}`` map from list_nodes.

    Returns:
        StreamResult with final event and accumulated node results.
    """
    from cli.interactive import prompt_for_file_upload, prompt_for_input, prompt_for_review

    out = output_console or get_console()
    result = initial_result

    # Build node_id -> (slug, step_type) lookup
    node_id_to_info: dict[str, tuple[str, str]] = {}
    if node_map:
        node_id_to_info = dict(node_map)
    for nr in result.nodes:
        if nr.node_id not in node_id_to_info:
            node_id_to_info[nr.node_id] = (nr.display_name, nr.step_type)

    while True:
        event_upper = result.final_event.upper()

        # Terminal states -- exit the loop
        if event_upper in _SSE_TERMINAL_EVENTS or event_upper in _TERMINAL_STATUSES:
            break

        if event_upper == 'WAITING_FOR_INPUT':
            # Get the paused node info from status API
            status_resp = _poll_with_retry(client, workflow_id, run_id, output_console=out)
            _state = status_resp.state or {}
            node_id = (
                _state.get('waiting_for_input_node_id')
                or _state.get('waiting_input_node_id')
                or _state.get('current_node_id', '')
            )
            node_slug, step_type = node_id_to_info.get(node_id, (node_id, ''))

            if step_type == 'FILE_UPLOAD':
                # File upload flow
                file_paths = prompt_for_file_upload(node_id, node_slug, step_type, console=out)
                if file_paths is None:
                    return result

                file_refs: list[dict[str, Any]] = []
                for fp in file_paths:
                    try:
                        out.print(f'[dim]Uploading {fp.name}...[/dim]', end=' ')
                        upload_resp = client.upload_file(
                            workflow_id,
                            node_id=node_id,
                            run_id=run_id,
                            file_path=fp,
                        )
                        file_refs.append(
                            {
                                'file_id': upload_resp.file_id,
                                'name': upload_resp.filename,
                                's3_uri': upload_resp.s3_uri,
                                'size': upload_resp.file_size,
                                'content_type': upload_resp.content_type,
                            }
                        )
                        out.print('[green]done[/green]')
                    except Exception as e:
                        out.print(f'[bold red]failed: {e}[/bold red]')
                        return result

                input_data: dict[str, Any] = {'files': file_refs, 'type': 'fileUpload'}
                try:
                    client.submit_input(
                        workflow_id,
                        run_id=run_id,
                        node_id=node_id,
                        input_data=input_data,
                    )
                    out.print('[green]Files submitted.[/green]')
                except Exception as e:
                    out.print(f'[bold red]Error submitting files:[/bold red] {e}')
                    return result
            else:
                # Existing text/JSON input flow
                data = prompt_for_input(node_id, node_slug, step_type, console=out)
                if data is None:
                    # User cancelled -- exit interactive loop
                    return result

                # Submit with retry
                try:
                    client.submit_input(
                        workflow_id,
                        run_id=run_id,
                        node_id=node_id,
                        input_data=data,
                    )
                    out.print('[green]Input submitted.[/green]')
                except Exception as e:
                    out.print(f'[bold red]Error submitting input:[/bold red] {e}')
                    if Confirm.ask('Retry submission?', default=True):
                        try:
                            client.submit_input(
                                workflow_id,
                                run_id=run_id,
                                node_id=node_id,
                                input_data=data,
                            )
                            out.print('[green]Input submitted.[/green]')
                        except Exception as retry_err:
                            out.print(f'[bold red]Retry failed:[/bold red] {retry_err}')
                            return result
                    else:
                        return result

        elif event_upper == 'WAITING_FOR_REVIEW':
            # Get the paused node info from status API
            status_resp = _poll_with_retry(client, workflow_id, run_id, output_console=out)
            node_id = status_resp.state.get('review_node_id', '')
            node_slug, step_type = node_id_to_info.get(node_id, (node_id, ''))

            review_result = prompt_for_review(node_id, node_slug, step_type, console=out)
            if review_result is None:
                # User cancelled -- exit interactive loop
                return result

            decision, comment = review_result
            # Submit with retry
            try:
                client.submit_review(
                    workflow_id, run_id=run_id, decision=decision, feedback=comment
                )
                out.print(f'[green]Review submitted: {decision}.[/green]')
            except Exception as e:
                out.print(f'[bold red]Error submitting review:[/bold red] {e}')
                if Confirm.ask('Retry submission?', default=True):
                    try:
                        client.submit_review(
                            workflow_id, run_id=run_id, decision=decision, feedback=comment
                        )
                        out.print(f'[green]Review submitted: {decision}.[/green]')
                    except Exception as retry_err:
                        out.print(f'[bold red]Retry failed:[/bold red] {retry_err}')
                        return result
                else:
                    return result
        else:
            # Unknown HITL state -- break to avoid infinite loop
            break

        # Resume monitoring via polling
        out.print()
        out.print('[dim]Resuming workflow monitoring...[/dim]')
        poll_status = _poll_until_next_event(
            client,
            workflow_id,
            run_id,
            verbose=verbose,
            output_console=out,
        )
        result = StreamResult(final_event=poll_status, nodes=result.nodes)

    return result


def _emit_run_json_result(
    *,
    run_id: str,
    workflow_id: str,
    final_status: str,
    status_resp: Any | None,
) -> None:
    """Emit the final machine-readable run result as one compact JSON line."""
    node_outputs: dict[str, Any] = {}
    if status_resp is not None:
        node_outputs = (status_resp.state or {}).get('node_outputs', {}) or {}

    print(
        json.dumps(
            {
                'run_id': run_id,
                'workflow_id': workflow_id,
                'final_status': final_status,
                'node_outputs': node_outputs,
            },
            separators=(',', ':'),
            default=str,
        ),
        flush=True,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_command(
    config: CLIConfig,
    identifier: str,
    input_data: str | None,
    *,
    stream: bool = False,
    interactive: bool = False,
    no_follow: bool = False,
    verbose: bool = False,
    json_output: bool = False,
    no_color: bool = False,
    working_dir: Path | None = None,
) -> None:
    """Main entry point for the run command.

    Args:
        config: CLI configuration with API credentials.
        identifier: Workflow UUID or name.
        input_data: JSON string or @filepath for initial inputs.
        stream: Use SSE streaming instead of polling.
        interactive: Enable interactive HITL mode (requires --stream + TTY).
        no_follow: Fire-and-forget mode.
        verbose: Use verbose multi-line SSE output.
        json_output: Emit NDJSON events and a final JSON result; suppress Rich output.
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

    if json_output and interactive:
        raise ValueError(
            '--json cannot be used with --interactive; interactive mode requires a TTY.'
        )

    # Validate interactive mode preconditions (--stream + TTY)
    if interactive:
        from cli.interactive import check_interactive_preconditions

        check_interactive_preconditions(stream, interactive, console=output_console)

    # Parse input
    inputs = parse_input_arg(input_data)

    with WorkflowClient.from_config(config) as client:
        # Resolve identifier to UUID
        workflow_id = resolve_workflow_id(identifier, client, config.org_id, search_dir=cwd)

        if not json_output:
            output_console.print(f'[bold cyan]Running workflow:[/bold cyan] {workflow_id}')

        # Fetch node count for progress display
        nodes: list[Any] = []
        try:
            nodes = client.list_nodes(workflow_id)
            total_nodes = len(nodes)
        except Exception:
            total_nodes = 0

        if stream:
            # SSE streaming mode
            run_id = str(uuid_mod.uuid4())
            if not json_output:
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

            with _stream_with_retry(
                client,
                workflow_id,
                run_id=run_id,
                inputs=inputs,
                output_console=output_console,
                quiet=json_output,
            ) as response:
                result = run_streaming(
                    response.iter_lines(),
                    total_nodes=total_nodes,
                    verbose=verbose,
                    output_console=output_console,
                    pending_input=inputs if inputs else None,
                    submit_input_fn=lambda nid, payload: client.submit_input(
                        workflow_id,
                        run_id=run_id,
                        node_id=nid,
                        input_data=payload,
                    ),
                    json_output=json_output,
                )
                final_status = result.final_event

            # Interactive mode: handle HITL gates inline
            if interactive and result.final_event.upper() in _SSE_HITL_EVENTS:
                # Build node_id -> (slug, step_type) map from the full node list
                node_map: dict[str, tuple[str, str]] = {}
                try:
                    node_list = client.list_nodes(workflow_id)
                    for n in node_list:
                        nid = str(n.id)
                        slug = n.slug if hasattr(n, 'slug') else str(n.id)
                        ntype = (
                            n.config_type.value
                            if hasattr(n.config_type, 'value')
                            else str(n.config_type)
                        )
                        node_map[nid] = (slug, ntype)
                except Exception:
                    pass  # Fall back to StreamResult node info

                result = _run_interactive(
                    client,
                    workflow_id,
                    run_id,
                    result,
                    total_nodes=total_nodes,
                    verbose=verbose,
                    output_console=output_console,
                    node_map=node_map if node_map else None,
                )
                final_status = result.final_event

                # Print summary table for interactive runs that reach RUN_FINISHED
                if result.final_event.upper() == 'RUN_FINISHED':
                    _print_summary_table(result.nodes, output_console)

        else:
            # Start workflow (polling or no-follow)
            start_resp = client.start_workflow_temporal(workflow_id, inputs=inputs)
            run_id = start_resp.run_id

            if not json_output:
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
                if json_output:
                    _emit_run_json_result(
                        run_id=run_id,
                        workflow_id=workflow_id,
                        final_status=start_resp.status,
                        status_resp=None,
                    )
                    return
                output_console.print(f'[green]Workflow started.[/green] Run ID: {run_id}')
                output_console.print(f'[dim]Check status: workflow status {run_id}[/dim]')
                if inputs:
                    output_console.print(
                        '[yellow]Note:[/yellow] --input data is not auto-submitted in --no-follow mode. '
                        f"Use: [cyan]workflow input {run_id} --data '{{...}}'[/cyan]"
                    )
                return

            # Polling mode
            if not json_output:
                output_console.print('[dim]Mode: polling (2s interval)[/dim]')
                output_console.print()
            final_status = run_polling(
                client,
                workflow_id,
                run_id,
                total_nodes=total_nodes,
                output_console=output_console,
                pending_input=inputs if inputs else None,
                json_output=json_output,
            )

        if json_output:
            final_resp = client.get_workflow_status(workflow_id, run_id)
            _emit_run_json_result(
                run_id=run_id,
                workflow_id=workflow_id,
                final_status=final_status,
                status_resp=final_resp,
            )
        else:
            # Fetch and display node outputs for completed runs
            if final_status.upper() in ('COMPLETED', 'RUN_FINISHED'):
                try:
                    final_resp = client.get_workflow_status(workflow_id, run_id)
                    _print_run_node_outputs(final_resp, nodes or None, output_console)
                except Exception:
                    pass  # Non-critical: don't fail the run for output display errors

            # Handle final status
            _print_final_status(final_status, run_id, output_console=output_console)

        # Exit with code 1 for failure statuses (case-insensitive)
        if final_status.upper() in _FAILURE_STATUSES:
            sys.exit(1)


# Statuses that indicate workflow failure (exit code 1)
_FAILURE_STATUSES = {'FAILED', 'RUN_ERROR', 'CANCELLED', 'TIMED_OUT', 'TIMEOUT'}


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


def _print_run_node_outputs(
    status_resp: WorkflowStatusResponse,
    nodes: list[Any] | None = None,
    output_console: Console | None = None,
) -> None:
    """Print node outputs from a completed run's final status.

    Args:
        status_resp: Workflow status response with state containing node_outputs.
        nodes: Optional list of LogicalNodePublic for slug-based display names.
        output_console: Optional Rich Console override.
    """
    out = output_console or get_console()
    node_outputs: dict[str, Any] = status_resp.state.get('node_outputs', {})
    if not node_outputs:
        return

    # Build node_id -> display name map
    node_names: dict[str, str] = {}
    if nodes:
        for node in nodes:
            nid = str(node.id)
            slug = getattr(node, 'slug', None)
            node_names[nid] = slug or (nid[:8] + '...' if len(nid) > 8 else nid)

    out.print()
    out.print('[bold]Node Outputs[/bold]')
    for node_id, output in node_outputs.items():
        display = node_names.get(node_id, node_id[:8] + '...' if len(node_id) > 8 else node_id)
        out.print(f'  [cyan]{display}[/cyan]:')
        formatted = _json.dumps(output, indent=2, default=str)
        for line in formatted.split('\n'):
            out.print(f'    {line}')
