"""Workflow CLI — main entry point."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

import typer
from rich.console import Console

from cli import __version__
from cli.config import CLIConfig, load_config, resolve_config

app = typer.Typer(
    name='workflow',
    help='CLI tool for managing and executing workflows on the Agents Platform.',
    no_args_is_help=True,
    rich_markup_mode='rich',
)
console = Console()

# Module-level state for the resolved config (set by the callback)
_config: CLIConfig | None = None


def get_config() -> CLIConfig:
    """Return the current resolved CLI config. Raises if not yet initialized."""
    if _config is None:
        return load_config()
    return _config


class FormatChoice(str, Enum):
    """Output format choices for the --format option."""

    json = 'json'
    yaml = 'yaml'


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f'workflow-cli v{__version__}')
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            '--version',
            '-v',
            help='Show the CLI version and exit.',
            callback=version_callback,
            is_eager=True,
        ),
    ] = False,
    host: Annotated[
        str | None,
        typer.Option(
            '--host',
            help='API host URL (overrides WORKFLOW_API_HOST env var).',
            envvar='WORKFLOW_API_HOST',
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            '--api-key',
            help='API authentication key (overrides WORKFLOW_API_KEY env var).',
            envvar='WORKFLOW_API_KEY',
        ),
    ] = None,
    org: Annotated[
        str | None,
        typer.Option(
            '--org',
            help='Organization ID (overrides WORKFLOW_ORG_ID env var).',
            envvar='WORKFLOW_ORG_ID',
        ),
    ] = None,
    output_format: Annotated[
        FormatChoice | None,
        typer.Option(
            '--format',
            help='Output format: json or yaml.',
        ),
    ] = None,
) -> None:
    """Agents Platform Workflow CLI."""
    global _config  # noqa: PLW0603

    # Load base config from file + env vars, then apply CLI flag overrides
    base = load_config()
    _config = resolve_config(
        base,
        host=host,
        api_key=api_key,
        org_id=org,
        output_format=output_format.value if output_format else None,
    )
