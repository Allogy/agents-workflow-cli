"""Workflow CLI — main entry point."""

import typer
from rich.console import Console

from cli import __version__

app = typer.Typer(
    name='workflow',
    help='CLI tool for managing and executing workflows on the Agents Platform.',
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f'workflow-cli v{__version__}')
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        '--version',
        '-v',
        help='Show the CLI version and exit.',
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Agents Platform Workflow CLI."""


@app.command()
def hello(name: str = typer.Argument('World', help='Name to greet.')) -> None:
    """Say hello — placeholder command to verify the CLI works."""
    console.print(f'[bold green]Hello, {name}![/bold green]')
