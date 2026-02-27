"""workflow list command implementation.

Lists workflows in the organization with various output formats.

Features:
- Table format with rich formatting (default)
- JSON format for scripting/piping
- YAML format for human-readable exports
- Organization filtering
- Sortable by updated timestamp

Usage:
    workflow list
    workflow list --format json
    workflow list --format yaml
"""

from __future__ import annotations

import json
from datetime import datetime

import yaml
from rich.console import Console
from rich.table import Table

from cli.client import WorkflowClient
from cli.config import CLIConfig

console = Console()


def format_datetime(dt: datetime | str | None) -> str:
    """Format a datetime for display.

    Args:
        dt: Datetime object, ISO string, or None.

    Returns:
        Formatted date string or 'N/A' if None.
    """
    if dt is None:
        return 'N/A'

    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except ValueError:
            return dt  # Return as-is if parsing fails

    return dt.strftime('%Y-%m-%d %H:%M')


def truncate_uuid(uuid_str: str, length: int = 8) -> str:
    """Truncate a UUID to the first N characters for display.

    Args:
        uuid_str: Full UUID string.
        length: Number of characters to keep (default 8).

    Returns:
        Truncated UUID string.
    """
    return str(uuid_str)[:length]


def list_workflows_table(config: CLIConfig) -> None:
    """List workflows in rich table format.

    Args:
        config: CLI configuration with API credentials.
    """
    with WorkflowClient.from_config(config) as client:
        workflows = client.list_workflows(organization_id=config.org_id)

        if not workflows:
            console.print('[yellow]No workflows found in this organization.[/yellow]')
            return

        # Fetch metadata for each workflow
        workflow_metadata = {}
        for workflow in workflows:
            try:
                metadata = client.get_metadata(workflow.id)
                workflow_metadata[str(workflow.id)] = metadata
            except Exception:
                # If metadata fetch fails, continue without it
                workflow_metadata[str(workflow.id)] = None

    # Create table
    table = Table(title='Workflows', show_header=True, header_style='bold magenta')
    table.add_column('Name', style='cyan', no_wrap=False)
    table.add_column('ID', style='dim', width=10)
    table.add_column('Updated', style='green')

    # Add rows
    for workflow in workflows:
        # Get name from metadata if available
        metadata = workflow_metadata.get(str(workflow.id))
        if metadata and metadata.name:
            name = metadata.name
        else:
            name = f'Workflow {truncate_uuid(str(workflow.id))}'

        table.add_row(
            name,
            truncate_uuid(str(workflow.id)),
            format_datetime(workflow.updated_at),
        )

    console.print(table)


def list_workflows_json(config: CLIConfig) -> None:
    """List workflows in JSON format.

    Args:
        config: CLI configuration with API credentials.
    """
    with WorkflowClient.from_config(config) as client:
        workflows = client.list_workflows(organization_id=config.org_id)

        # Fetch metadata for each workflow
        workflow_metadata = {}
        for workflow in workflows:
            try:
                metadata = client.get_metadata(workflow.id)
                workflow_metadata[str(workflow.id)] = metadata
            except Exception:
                # If metadata fetch fails, continue without it
                workflow_metadata[str(workflow.id)] = None

    # Convert to JSON-serializable format
    output = []
    for workflow in workflows:
        # Handle datetime serialization
        created_at = workflow.created_at
        if created_at and hasattr(created_at, 'isoformat'):
            created_at = created_at.isoformat()

        updated_at = workflow.updated_at
        if updated_at and hasattr(updated_at, 'isoformat'):
            updated_at = updated_at.isoformat()

        # Get metadata if available
        metadata = workflow_metadata.get(str(workflow.id))

        output.append(
            {
                'id': str(workflow.id),
                'version': workflow.version,
                'organization_id': str(workflow.organization_id),
                'created_by': str(workflow.created_by),
                'created_at': created_at,
                'updated_at': updated_at,
                # Include metadata if available
                'name': metadata.name if metadata else None,
                'description': metadata.description if metadata else None,
            }
        )

    # Print JSON (suitable for piping)
    print(json.dumps(output, indent=2))


def list_workflows_yaml(config: CLIConfig) -> None:
    """List workflows in YAML format.

    Args:
        config: CLI configuration with API credentials.
    """
    with WorkflowClient.from_config(config) as client:
        workflows = client.list_workflows(organization_id=config.org_id)

        # Fetch metadata for each workflow
        workflow_metadata = {}
        for workflow in workflows:
            try:
                metadata = client.get_metadata(workflow.id)
                workflow_metadata[str(workflow.id)] = metadata
            except Exception:
                # If metadata fetch fails, continue without it
                workflow_metadata[str(workflow.id)] = None

    # Convert to YAML-serializable format
    output = []
    for workflow in workflows:
        # Handle datetime serialization
        created_at = workflow.created_at
        if created_at and hasattr(created_at, 'isoformat'):
            created_at = created_at.isoformat()

        updated_at = workflow.updated_at
        if updated_at and hasattr(updated_at, 'isoformat'):
            updated_at = updated_at.isoformat()

        # Get metadata if available
        metadata = workflow_metadata.get(str(workflow.id))

        output.append(
            {
                'id': str(workflow.id),
                'version': workflow.version,
                'organization_id': str(workflow.organization_id),
                'created_by': str(workflow.created_by),
                'created_at': created_at,
                'updated_at': updated_at,
                # Include metadata if available
                'name': metadata.name if metadata else None,
                'description': metadata.description if metadata else None,
            }
        )

    # Print YAML
    print(yaml.dump(output, default_flow_style=False, sort_keys=False))


def list_command(config: CLIConfig, output_format: str | None = None) -> None:
    """Main entry point for list command.

    Args:
        config: CLI configuration with API credentials.
        output_format: Output format ('json', 'yaml', or None for table).
            Falls back to config.output_format when None.

    Raises:
        Exception: If API call fails or configuration is invalid.
    """
    # Validate config has required fields
    config.validate_for_api()

    # Fall back to global --format flag from config when command-level flag is absent
    fmt = output_format or (config.output_format.value if config.output_format else None)

    # Route to appropriate formatter
    if fmt == 'json':
        list_workflows_json(config)
    elif fmt == 'yaml':
        list_workflows_yaml(config)
    else:
        list_workflows_table(config)
