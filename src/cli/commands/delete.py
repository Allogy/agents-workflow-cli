"""workflow delete command implementation.

Deletes a workflow by ID or name with optional confirmation.

Features:
- Delete by UUID (exact match)
- Delete by name (fuzzy matching, case-insensitive)
- Confirmation prompt (Rich.Confirm)
- Force flag to skip confirmation
- Clear error messages for not-found workflows

Usage:
    workflow delete <uuid>
    workflow delete "Workflow Name"
    workflow delete <uuid> --force
"""

from __future__ import annotations

from uuid import UUID

from rich.console import Console
from rich.prompt import Confirm

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.exceptions import NotFoundError

console = Console()


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID.

    Args:
        value: String to check.

    Returns:
        True if valid UUID, False otherwise.
    """
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def find_workflow_by_name(client: WorkflowClient, name: str, org_id: str) -> str | None:
    """Find a workflow ID by name using fuzzy matching.

    Args:
        client: WorkflowClient instance.
        name: Workflow name to search for.
        org_id: Organization ID to filter by.

    Returns:
        Workflow ID if found, None otherwise.

    Raises:
        ValueError: If multiple exact matches found (ambiguous).
    """
    workflows = client.list_workflows(organization_id=org_id)

    # First try exact match (case-insensitive)
    exact_matches = [w for w in workflows if w.name and w.name.lower() == name.lower()]

    if len(exact_matches) == 1:
        return str(exact_matches[0].id)
    if len(exact_matches) > 1:
        raise ValueError(
            f'Multiple workflows found with name "{name}". Please use the workflow ID instead.'
        )

    # Try partial match (case-insensitive)
    partial_matches = [w for w in workflows if w.name and name.lower() in w.name.lower()]

    if len(partial_matches) == 1:
        return str(partial_matches[0].id)
    if len(partial_matches) > 1:
        match_names = [w.name for w in partial_matches]
        raise ValueError(
            f'Multiple workflows found matching "{name}": {", ".join(match_names)}. '
            f'Please be more specific or use the workflow ID.'
        )

    # No matches found
    return None


def delete_command(config: CLIConfig, identifier: str, force: bool = False) -> None:
    """Main entry point for delete command.

    Args:
        config: CLI configuration with API credentials.
        identifier: Workflow UUID or name.
        force: If True, skip confirmation prompt.

    Raises:
        ValueError: If configuration invalid or workflow not found.
        NotFoundError: If workflow doesn't exist.
    """
    # Validate config has required fields
    config.validate_for_api()

    with WorkflowClient.from_config(config) as client:
        # Determine if identifier is UUID or name
        if is_valid_uuid(identifier):
            workflow_id = identifier

            # Fetch workflow to verify it exists and get its name
            try:
                workflow = client.get_workflow(workflow_id)
                workflow_name = workflow.name or 'Untitled'
            except Exception as e:
                console.print(f'[bold red]Error:[/bold red] Workflow not found: {identifier}')
                raise NotFoundError(f'Workflow {identifier} not found') from e
        else:
            # Search by name
            workflow_name = identifier
            try:
                workflow_id = find_workflow_by_name(client, identifier, config.org_id)  # type: ignore[arg-type]
            except ValueError as e:
                console.print(f'[bold red]Error:[/bold red] {e}')
                raise

            if workflow_id is None:
                console.print(
                    f'[bold red]Error:[/bold red] No workflow found with name "{identifier}"'
                )
                raise NotFoundError(f'Workflow "{identifier}" not found')

        # Show confirmation prompt (unless --force)
        if not force:
            confirm_msg = (
                f'Are you sure you want to delete workflow "{workflow_name}" '
                f'({workflow_id[:8]}...)?'
            )
            confirmed = Confirm.ask(confirm_msg, default=False)

            if not confirmed:
                console.print('[yellow]Delete cancelled.[/yellow]')
                return

        # Perform deletion
        try:
            client.delete_workflow(workflow_id)
            console.print(
                f'[bold green]✓[/bold green] Successfully deleted workflow '
                f'"{workflow_name}" ({workflow_id[:8]}...)'
            )
        except Exception as e:
            console.print(f'[bold red]Error:[/bold red] Failed to delete workflow: {e}')
            raise
