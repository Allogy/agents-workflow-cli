"""Validate command for workflow CLI.

Validates a .workflow.yaml file offline with no API calls.

Usage:
    workflow validate my-workflow.workflow.yaml

Exit codes:
    0 - All checks passed (warnings allowed)
    1 - One or more checks failed

Reference: Jira RAG-947
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cli.validation import CheckStatus, run_all_validations

console = Console()


def validate_command(file_path: Path) -> None:
    """Validate a workflow definition file.

    Args:
        file_path: Path to .workflow.yaml file to validate.
    """
    # Check file exists
    if not file_path.exists():
        console.print(f'[red]Error:[/red] File not found: {file_path}')
        raise typer.Exit(code=1)

    # Read file
    try:
        yaml_content = file_path.read_text()
    except OSError as e:
        console.print(f'[red]Error reading file:[/red] {e}')
        raise typer.Exit(code=1) from None

    # Run validations
    console.print(f'[bold]Validating:[/bold] {file_path}')
    console.print()
    results = run_all_validations(yaml_content)

    # Display results in a table
    table = Table(show_header=True, header_style='bold')
    table.add_column('Check', style='cyan', no_wrap=True)
    table.add_column('Status', no_wrap=True)
    table.add_column('Details')

    has_failures = False
    for result in results:
        # Determine style based on status
        if result.status == CheckStatus.PASS:
            status_text = '[green]✓ PASS[/green]'
        elif result.status == CheckStatus.WARN:
            status_text = '[yellow]⚠ WARN[/yellow]'
        else:  # FAIL
            status_text = '[red]✗ FAIL[/red]'
            has_failures = True

        # Format details
        details = result.message if result.message else ''

        table.add_row(result.check_name, status_text, details)

    console.print(table)
    console.print()

    # Summary
    pass_count = sum(1 for r in results if r.status == CheckStatus.PASS)
    warn_count = sum(1 for r in results if r.status == CheckStatus.WARN)
    fail_count = sum(1 for r in results if r.status == CheckStatus.FAIL)

    if has_failures:
        console.print(
            f'[red]Validation failed:[/red] {fail_count} failures, '
            f'{warn_count} warnings, {pass_count} passed'
        )
        raise typer.Exit(code=1)
    elif warn_count > 0:
        console.print(
            f'[yellow]Validation passed with warnings:[/yellow] '
            f'{warn_count} warnings, {pass_count} passed'
        )
        raise typer.Exit(code=0)
    else:
        console.print(f'[green]Validation passed:[/green] All {pass_count} checks passed')
        raise typer.Exit(code=0)
