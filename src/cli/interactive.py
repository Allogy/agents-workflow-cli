"""Interactive HITL prompting module.

Provides user-facing prompt functions for collecting input data and review
decisions during interactive streaming mode.  Each function is independently
testable without mocking the full streaming/polling pipeline.

Public API:
    check_interactive_preconditions  -- validate --stream + TTY requirements
    prompt_for_input                 -- collect JSON/@file data interactively
    prompt_for_review                -- collect review decision interactively
    prompt_for_file_upload           -- collect file paths interactively
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from cli.commands.run import parse_input_arg
from cli.console import get_console


def check_interactive_preconditions(
    stream: bool,
    interactive: bool,
    console: Console | None = None,
) -> None:
    """Validate preconditions for interactive mode.

    Must be called BEFORE any streaming begins.

    Args:
        stream: Whether ``--stream`` was passed.
        interactive: Whether ``--interactive`` was passed.
        console: Optional Rich console for output.

    Raises:
        ValueError: If ``--interactive`` is used without ``--stream``.
        SystemExit: (code 2) If stdin is not a TTY.
    """
    if interactive and not stream:
        raise ValueError(
            '--interactive requires --stream. Use: workflow run <id> --stream --interactive'
        )

    if interactive and not sys.stdin.isatty():
        out = console or get_console()
        out.print()
        out.print('[bold red]Error:[/bold red] Interactive mode requires a terminal (TTY).')
        out.print()
        out.print(
            'Detected non-interactive environment (piped input or CI). '
            'Interactive prompts cannot be displayed without a terminal.'
        )
        out.print()
        out.print('[bold]Alternatives:[/bold]')
        out.print('  1. [cyan]workflow run <id> --stream[/cyan]  (non-interactive streaming)')
        out.print(
            '  2. [cyan]workflow input[/cyan] / [cyan]workflow review[/cyan]  '
            '(separate HITL commands)'
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# Review decision mapping
# ---------------------------------------------------------------------------
_REVIEW_CHOICES: dict[int, str] = {1: 'approve', 2: 'reject', 3: 'revise'}


def prompt_for_input(
    node_id: str,
    node_slug: str,
    step_type: str,
    console: Console | None = None,
) -> dict[str, Any] | None:
    """Collect input data interactively.

    Presents a prompt loop that accepts a JSON string or ``@filepath``,
    validates the input via :func:`parse_input_arg`, and asks for Y/N
    confirmation before returning.

    Args:
        node_id: UUID of the INPUT node (used in skip-hint).
        node_slug: Human-readable node slug (displayed to user).
        step_type: Node step type (displayed to user).
        console: Optional Rich console for output.

    Returns:
        Parsed dict on success, or ``None`` if the user cancels or skips.
    """
    out = console or get_console()

    out.print()
    out.print('[bold yellow]Input required[/bold yellow]')
    out.print(f'Node: [cyan]{node_slug}[/cyan] ({step_type})')
    out.print('[dim]Enter JSON string or @filepath. Ctrl+C to skip.[/dim]')

    try:
        while True:
            raw = Prompt.ask('[bold]Data[/bold]')
            try:
                parsed = parse_input_arg(raw)
            except (ValueError, FileNotFoundError) as exc:
                out.print(f'[red]{exc}[/red]')
                out.print('[dim]Try again or press Ctrl+C to skip.[/dim]')
                continue

            if Confirm.ask(f'Submit input to {node_slug}?', default=True):
                return parsed

            out.print('[yellow]Cancelled.[/yellow]')
            return None
    except KeyboardInterrupt:
        out.print()
        out.print(
            f'[yellow]Skipped.[/yellow] '
            f"[dim]Use: workflow input --node-id {node_id} --data '{{...}}' later.[/dim]"
        )
        return None


def prompt_for_review(
    node_id: str,
    node_slug: str,
    step_type: str,
    console: Console | None = None,
) -> tuple[str, str | None] | None:
    """Collect a review decision interactively.

    Presents a numbered menu (Approve / Reject / Request revision), collects
    an optional comment for reject/revise, and asks for Y/N confirmation.

    Args:
        node_id: UUID of the HUMAN_REVIEW node (used in skip-hint).
        node_slug: Human-readable node slug (displayed to user).
        step_type: Node step type (displayed to user).
        console: Optional Rich console for output.

    Returns:
        ``(decision, comment_or_none)`` on success, or ``None`` if cancelled.
    """
    out = console or get_console()

    out.print()
    out.print('[bold yellow]Review required[/bold yellow]')
    out.print(f'Node: [cyan]{node_slug}[/cyan] ({step_type})')
    out.print()
    out.print('  [bold]1.[/bold] Approve')
    out.print('  [bold]2.[/bold] Reject')
    out.print('  [bold]3.[/bold] Request revision')
    out.print()

    try:
        choice = IntPrompt.ask('[bold]Select action[/bold]', choices=['1', '2', '3'])
        decision = _REVIEW_CHOICES[choice]

        comment: str | None = None
        if decision in ('reject', 'revise'):
            comment = Prompt.ask(f'[dim]Comment for {decision}[/dim]')

        if Confirm.ask(f'Submit review: {decision}?', default=True):
            return (decision, comment)

        out.print('[yellow]Cancelled.[/yellow]')
        return None
    except KeyboardInterrupt:
        out.print()
        out.print(
            f'[yellow]Skipped.[/yellow] '
            f'[dim]Use: workflow review --run-id <id> --node-id {node_id} --approve later.[/dim]'
        )
        return None


def _format_file_size(size_bytes: int) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f'{size_bytes} B'
    elif size_bytes < 1024 * 1024:
        return f'{size_bytes / 1024:.1f} KB'
    else:
        return f'{size_bytes / (1024 * 1024):.1f} MB'


def prompt_for_file_upload(
    node_id: str,
    node_slug: str,
    step_type: str,
    accepted_formats: list[str] | None = None,
    max_file_size: int | None = None,
    console: Console | None = None,
) -> list[Path] | None:
    """Collect file paths interactively for a file_upload node.

    Presents a prompt loop that accepts file paths, validates each file
    (existence, extension, size), and allows the user to add multiple files
    via an "Add another?" confirmation loop.

    Args:
        node_id: UUID of the FILE_UPLOAD node (used in skip-hint).
        node_slug: Human-readable node slug (displayed to user).
        step_type: Node step type (displayed to user).
        accepted_formats: Optional list of accepted file extensions (e.g. ``['.pdf', '.docx']``).
            Compared case-insensitively.  ``None`` means any extension is accepted.
        max_file_size: Optional maximum file size in bytes.  ``None`` means no limit.
        console: Optional Rich console for output.

    Returns:
        List of validated :class:`~pathlib.Path` objects on success, or ``None``
        if the user cancels via Ctrl+C.
    """
    out = console or get_console()

    out.print()
    out.print('[bold yellow]File upload required[/bold yellow]')
    out.print(f'Node: [cyan]{node_slug}[/cyan] ({step_type})')
    if accepted_formats:
        fmt_str = ', '.join(accepted_formats)
        out.print(f'Accepted formats: [cyan]{fmt_str}[/cyan]')
    if max_file_size is not None:
        out.print(f'Max file size: [cyan]{_format_file_size(max_file_size)}[/cyan]')
    out.print('[dim]Enter file path. Ctrl+C to skip.[/dim]')

    collected: list[Path] = []

    try:
        while True:
            raw = Prompt.ask('[bold]File path[/bold]')
            path = Path(raw).expanduser()

            # Validate: file exists
            if not path.exists():
                out.print(f'[red]File does not exist: {path}[/red]')
                out.print('[dim]Try again or press Ctrl+C to skip.[/dim]')
                continue

            if not path.is_file():
                out.print(f'[red]Not a file: {path}[/red]')
                out.print('[dim]Try again or press Ctrl+C to skip.[/dim]')
                continue

            # Validate: extension
            if accepted_formats is not None:
                normalised_formats = [f.lower() for f in accepted_formats]
                if path.suffix.lower() not in normalised_formats:
                    out.print(
                        f'[red]Invalid format: {path.suffix}. '
                        f'Accepted: {", ".join(accepted_formats)}[/red]'
                    )
                    out.print('[dim]Try again or press Ctrl+C to skip.[/dim]')
                    continue

            # Validate: file size
            if max_file_size is not None:
                file_size = path.stat().st_size
                if file_size > max_file_size:
                    out.print(
                        f'[red]File too large: {_format_file_size(file_size)}. '
                        f'Max allowed: {_format_file_size(max_file_size)}[/red]'
                    )
                    out.print('[dim]Try again or press Ctrl+C to skip.[/dim]')
                    continue

            collected.append(path)
            out.print(f'[green]Added: {path.name}[/green]')

            if not Confirm.ask('Add another file?', default=False):
                return collected

    except KeyboardInterrupt:
        out.print()
        out.print(
            f'[yellow]Skipped.[/yellow] '
            f'[dim]Use: workflow input --node-id {node_id} --data @filepath later.[/dim]'
        )
        return None
