"""Init command for workflow CLI.

Scaffolds a new workflow from a built-in template.

Usage:
    workflow init --template rag-qa -o my-workflow.workflow.yaml
    workflow init --list
    workflow init  (interactive mode)

Reference: Jira RAG-948
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cli.templates import get_template_info, list_templates, load_template_yaml

console = Console()


def init_command(
    template: str | None,
    output: Path | None,
    force: bool,
    list_mode: bool,
) -> None:
    """Scaffold a new workflow from a built-in template.

    Args:
        template: Template name (e.g. 'rag-qa'). If None, shows interactive picker.
        output: Output file path. Defaults to {template-name}.workflow.yaml.
        force: If True, overwrite existing files.
        list_mode: If True, just list available templates and exit.
    """
    # --list mode: show available templates and exit
    if list_mode:
        _show_template_list()
        raise typer.Exit(code=0)

    # If no template specified, show list and prompt
    if template is None:
        _show_template_list()
        console.print()
        console.print(
            '[dim]Use [bold]workflow init --template <name>[/bold] to scaffold a workflow.[/dim]'
        )
        raise typer.Exit(code=0)

    # Validate template name
    info = get_template_info(template)
    if info is None:
        available = ', '.join(t.name for t in list_templates())
        console.print(
            f'[red]Error:[/red] Unknown template: [bold]{template}[/bold]\n'
            f'Available templates: {available}'
        )
        raise typer.Exit(code=1)

    # Determine output path
    if output is None:
        output = Path(f'{template}.workflow.yaml')

    # Check if file exists
    if output.exists() and not force:
        console.print(
            f'[red]Error:[/red] File already exists: [bold]{output}[/bold]\n'
            f'Use [bold]--force[/bold] to overwrite.'
        )
        raise typer.Exit(code=1)

    # Load and write template
    try:
        yaml_content = load_template_yaml(template)
    except (KeyError, FileNotFoundError) as e:
        console.print(f'[red]Error:[/red] {e}')
        raise typer.Exit(code=1) from None

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(yaml_content)
    except OSError as e:
        console.print(f'[red]Error writing file:[/red] {e}')
        raise typer.Exit(code=1) from None

    console.print(f'[green]Created:[/green] {output} (from [bold]{template}[/bold] template)')
    raise typer.Exit(code=0)


def _show_template_list() -> None:
    """Display available templates in a Rich table."""
    table = Table(
        title='Available Templates',
        show_header=True,
        header_style='bold',
    )
    table.add_column('Template', style='cyan', no_wrap=True)
    table.add_column('Description')
    table.add_column('Nodes', style='dim')

    for info in list_templates():
        table.add_row(info.name, info.description, info.nodes)

    console.print(table)
