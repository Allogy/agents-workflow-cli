"""Validate command for workflow CLI.

Validates a .workflow.yaml file with optional registry-augmented checks.

Usage:
    workflow validate my-workflow.workflow.yaml
    workflow validate my-workflow.workflow.yaml --offline

Exit codes:
    0 - All checks passed (warnings allowed)
    1 - One or more checks failed

Reference: Jira RAG-947
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cli.registry import get_registry
from cli.validation import CheckStatus, run_all_validations

console = Console()


def validate_command(file_path: Path, *, offline: bool = False) -> None:
    """Validate a workflow definition file.

    Args:
        file_path: Path to .workflow.yaml file to validate.
        offline: If True, skip all registry-powered checks.
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

    # Fetch or load registry data (auto-fetch on first run or TTL expiry)
    from cli.main import get_config

    config = get_config()
    registry_result = get_registry(config.host, offline=offline)

    # Warn if using stale cache data
    if registry_result is not None and registry_result.is_stale:
        console.print(
            '[yellow]Warning:[/yellow] Registry cache expired \u2014 '
            'run [bold]workflow registry refresh[/bold] to update'
        )
        console.print()

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
            status_text = '[green]\u2713 PASS[/green]'
        elif result.status == CheckStatus.WARN:
            status_text = '[yellow]\u26a0 WARN[/yellow]'
        elif result.status == CheckStatus.SKIP:
            status_text = '[dim]- SKIP[/dim]'
        else:  # FAIL
            status_text = '[red]\u2717 FAIL[/red]'
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
    skip_count = sum(1 for r in results if r.status == CheckStatus.SKIP)

    if has_failures:
        console.print(
            f'[red]Validation failed:[/red] {fail_count} failures, '
            f'{warn_count} warnings, {skip_count} skipped, {pass_count} passed'
        )
        raise typer.Exit(code=1)
    elif warn_count > 0:
        console.print(
            f'[yellow]Validation passed with warnings:[/yellow] '
            f'{warn_count} warnings, {skip_count} skipped, {pass_count} passed'
        )
        raise typer.Exit(code=0)
    elif skip_count > 0:
        console.print(
            f'[green]Validation passed:[/green] {pass_count} passed, {skip_count} skipped'
        )
        raise typer.Exit(code=0)
    else:
        console.print(f'[green]Validation passed:[/green] All {pass_count} checks passed')
        raise typer.Exit(code=0)
