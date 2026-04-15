"""Registry subcommand group for managing the node type registry cache.

Subcommands:
    refresh -- Force-fetch the registry and update the local cache.
    status  -- Show registry cache status (validity, age, TTL, node types).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import typer

from cli.console import get_console
from cli.registry import (
    CACHE_PATH,
    RegistryCache,
    fetch_registry,
    load_cache,
    save_cache,
)

registry_app = typer.Typer(
    name='registry',
    help='Manage the node type registry cache.',
    no_args_is_help=True,
)


def _format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string (e.g., '2h 15m' or '3d 12h')."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return '0m'
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f'{days}d {hours}h'
    if hours > 0:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


@registry_app.command()
def refresh() -> None:
    """Force-fetch the registry and update the local cache."""
    from cli.main import get_config

    console = get_console()
    config = get_config()

    if config.host is None:
        console.print(
            '[red]Error:[/red] No host configured. '
            'Set --host, WORKFLOW_API_HOST, or host in ~/.workflow/config.yaml'
        )
        raise typer.Exit(1)

    try:
        data = fetch_registry(config.host)
    except httpx.TransportError as e:
        console.print(f'[red]Error:[/red] Could not reach {config.host} \u2014 {e}')
        raise typer.Exit(1) from None
    except httpx.HTTPStatusError as e:
        console.print(f'[red]Error:[/red] Registry endpoint returned {e.response.status_code}')
        raise typer.Exit(1) from None

    cache = RegistryCache(
        fetched_at=datetime.now(UTC),
        host=config.host.rstrip('/'),
        ttl_hours=24,
        registry=data,
    )
    save_cache(cache)

    count = len(data.get('all_node_types', []))
    version = data.get('version', 'unknown')

    console.print(f'Registry updated: {count} node types cached')
    console.print(f'Version: {version}')
    console.print(f'Cache: {CACHE_PATH}')


@registry_app.command()
def status() -> None:
    """Show registry cache status (validity, age, TTL, node types)."""
    console = get_console()
    cache = load_cache()

    if cache is None:
        console.print(
            'No registry cache found. Run [bold]workflow registry refresh[/bold] to fetch.'
        )
        raise typer.Exit(0)

    now = datetime.now(UTC)
    age = now - cache.fetched_at
    expired = cache.is_expired()
    ttl_td = timedelta(hours=cache.ttl_hours)

    if expired:
        validity = '[yellow]expired[/yellow]'
        ttl_remaining = 'expired'
    else:
        validity = '[green]valid[/green]'
        remaining = ttl_td - age
        ttl_remaining = _format_duration(remaining)

    all_node_types = cache.registry.get('all_node_types', [])
    total = len(all_node_types)
    active = sum(1 for nt in all_node_types if nt.get('status') == 'active')
    inactive = total - active
    version = cache.registry.get('version', 'unknown')

    console.print(f'Cache: {validity}')
    console.print(f'Age: {_format_duration(age)}')
    console.print(f'TTL remaining: {ttl_remaining}')
    console.print(f'Node types: {total} total ({active} active, {inactive} inactive)')
    console.print(f'Version: {version}')
    console.print(f'Endpoint: {cache.host}')
    console.print(f'Cache file: {CACHE_PATH}')
