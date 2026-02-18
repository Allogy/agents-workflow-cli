"""workflow push command implementation.

Pushes a local .workflow.yaml file to the platform, creating or updating
the workflow using the atomic save endpoint.

Features:
- Local validation before any API calls
- Dependency resolution (agent/KB names -> UUIDs)
- Lockfile integration for idempotent updates
- Auto-generated node layout positions
- Rich progress display
- Error handling with rollback

Usage:
    workflow push my-workflow.workflow.yaml
    workflow push  # Uses current directory *.workflow.yaml
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from rich.console import Console
from workflow_models.wdf import WorkflowDefinition

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.lockfile import WorkflowLock, get_lockfile_path, load_lockfile, save_lockfile
from cli.validation.runner import run_all_validations
from cli.wdf_yaml import load_workflow_yaml

console = Console()


class PushError(Exception):
    """Raised when push operation fails."""


class DependencyResolutionError(PushError):
    """Raised when a dependency (agent/KB) cannot be resolved."""


def resolve_dependencies(workflow: WorkflowDefinition, client: WorkflowClient) -> dict[str, UUID]:
    """Resolve agent and KB names to UUIDs.

    Args:
        workflow: The WorkflowDefinition with node configs referencing names.
        client: WorkflowClient for API lookups.

    Returns:
        Dictionary mapping reference names to UUIDs.
        Keys are in the format 'agent:{name}' or 'kb:{name}'.

    Raises:
        DependencyResolutionError: If any dependency cannot be resolved.
    """
    resolved: dict[str, UUID] = {}

    # Scan all nodes for agent and knowledge base references
    for _node_slug, node_def in workflow.nodes.items():
        config = node_def.config

        # Check for agent_name in config
        if 'agent_name' in config:
            agent_name = config['agent_name']
            if not isinstance(agent_name, str):
                continue

            key = f'agent:{agent_name}'
            if key not in resolved:
                result = client.find_agent_by_name(agent_name)
                if result is None:
                    raise DependencyResolutionError(f'Agent not found: {agent_name}')
                resolved[key] = UUID(result['id'])

        # Check for knowledge_base_name in config
        if 'knowledge_base_name' in config:
            kb_name = config['knowledge_base_name']
            if not isinstance(kb_name, str):
                continue

            key = f'kb:{kb_name}'
            if key not in resolved:
                result = client.find_knowledge_base_by_name(kb_name)
                if result is None:
                    raise DependencyResolutionError(f'Knowledge base not found: {kb_name}')
                resolved[key] = UUID(result['id'])

    return resolved


def generate_node_layout(workflow: WorkflowDefinition) -> dict[str, tuple[int, int]]:
    """Auto-generate node layout positions.

    Uses a simple vertical layout algorithm:
    - Entry nodes at top
    - Subsequent nodes spaced vertically
    - Horizontal spacing for parallel paths

    Args:
        workflow: The WorkflowDefinition to layout.

    Returns:
        Dictionary mapping node slugs to (x, y) positions.
    """
    layout: dict[str, tuple[int, int]] = {}
    y_offset = 100
    y_spacing = 150

    # Simple vertical layout for now
    for idx, (slug, _node) in enumerate(workflow.nodes.items()):
        layout[slug] = (200, y_offset + idx * y_spacing)

    return layout


def wdf_to_api_payload(
    workflow: WorkflowDefinition,
    resolved_deps: dict[str, UUID],
    layout: dict[str, tuple[int, int]],
    org_id: UUID,
    existing_workflow_id: UUID | None = None,
) -> dict[str, Any]:
    """Convert WDF model to SaveCompleteWorkflowRequest payload.

    Args:
        workflow: The WorkflowDefinition from YAML.
        resolved_deps: Dependency name -> UUID mappings.
        layout: Node slug -> (x, y) position mappings.
        org_id: Organization ID for the workflow.
        existing_workflow_id: If updating, the existing workflow UUID.

    Returns:
        Dictionary payload ready for POST /v1/workflows/complete.
    """
    payload: dict[str, Any] = {
        'workflow': {
            'name': workflow.name,
            'description': workflow.description or '',
            'version': workflow.version,
            'organization_id': str(org_id),
        },
        'nodes': [],
        'node_inputs': [],
        'node_outputs': [],
        'edges': [],
        'node_visuals': [],
        'edge_visuals': [],
    }

    if existing_workflow_id:
        payload['workflow']['id'] = str(existing_workflow_id)

    # Convert nodes
    for node_slug, node_def in workflow.nodes.items():
        # Clone config and resolve dependencies
        node_config = node_def.config.copy()

        # Replace agent_name with agent_id
        if 'agent_name' in node_config:
            agent_name = node_config.pop('agent_name')
            agent_key = f'agent:{agent_name}'
            if agent_key in resolved_deps:
                node_config['agent_id'] = str(resolved_deps[agent_key])

        # Replace knowledge_base_name with knowledge_base_id
        if 'knowledge_base_name' in node_config:
            kb_name = node_config.pop('knowledge_base_name')
            kb_key = f'kb:{kb_name}'
            if kb_key in resolved_deps:
                node_config['knowledge_base_id'] = str(resolved_deps[kb_key])

        node_payload = {
            'slug': node_slug,
            'node_config_type': node_def.type,
            'execution_mode': node_def.execution_mode,
            'label': node_def.label,
            'config': node_config,
        }
        payload['nodes'].append(node_payload)

        # Generate node I/O based on node type
        # Most nodes have at least one output
        if node_def.type not in ['structured_output']:
            payload['node_outputs'].append(
                {
                    'node_slug': node_slug,
                    'name': 'output',
                    'data_type': 'any',
                }
            )

        # Most nodes (except input nodes) have at least one input
        if node_def.type not in ['plain_txt_input', 'structured_input', 'file_upload']:
            payload['node_inputs'].append(
                {
                    'node_slug': node_slug,
                    'name': 'input',
                    'data_type': 'any',
                }
            )

        # Add node visuals (position from layout)
        if node_slug in layout:
            x, y = layout[node_slug]
            payload['node_visuals'].append(
                {
                    'node_slug': node_slug,
                    'position_x': x,
                    'position_y': y,
                }
            )

    # Convert edges
    for edge_def in workflow.edges:
        edge_payload = {
            'source_node_slug': edge_def.from_node,
            'target_node_slug': edge_def.to,
            'edge_type': edge_def.type or 'STATIC',
        }
        payload['edges'].append(edge_payload)

    return payload


def push_workflow(
    file_path: Path,
    config: CLIConfig,
) -> None:
    """Push a workflow to the platform.

    Orchestrates the complete push process:
    1. Load and validate YAML
    2. Check lockfile (create vs. update mode)
    3. Resolve dependencies
    4. Convert WDF to API payload
    5. Call atomic save endpoint
    6. Generate layout
    7. Write/update lockfile

    Args:
        file_path: Path to the .workflow.yaml file.
        config: Resolved CLI configuration.

    Raises:
        PushError: If any step fails.
    """
    console.print(f'[bold cyan]Pushing workflow:[/bold cyan] {file_path}')

    # Step 1: Load and validate YAML
    console.print('[dim]Validating...[/dim]', end=' ')
    try:
        yaml_content = file_path.read_text()
        workflow = load_workflow_yaml(yaml_content)
    except Exception as e:
        console.print('[bold red]failed[/bold red]')
        raise PushError(f'Failed to load workflow: {e}') from e

    # Run local validation
    from cli.validation.runner import CheckStatus

    validation_results = run_all_validations(yaml_content)
    errors = [r for r in validation_results if r.status == CheckStatus.FAIL]
    warnings = [r for r in validation_results if r.status == CheckStatus.WARN]

    if errors:
        console.print('[bold red]failed[/bold red]')
        console.print('[red]Validation errors:[/red]')
        for error in errors:
            console.print(f'  • {error.check_name}: {error.message}')
        raise PushError('Local validation failed')
    console.print('[green]✓[/green]')

    if warnings:
        console.print('[yellow]Warnings:[/yellow]')
        for warning in warnings:
            console.print(f'  • {warning.check_name}: {warning.message}')

    # Step 2: Check lockfile
    lockfile_path = get_lockfile_path(file_path)
    existing_lock = load_lockfile(lockfile_path)
    if existing_lock:
        console.print(f'[dim]Update mode (workflow {existing_lock.workflow_id})[/dim]')
        workflow_id = existing_lock.workflow_id
        org_id_from_lock = existing_lock.organization_id
    else:
        console.print('[dim]Create mode (new workflow)[/dim]')
        workflow_id = None
        org_id_from_lock = None

    # Step 3: Resolve dependencies
    console.print('[dim]Resolving dependencies...[/dim]', end=' ')
    with WorkflowClient.from_config(config) as client:
        try:
            resolved_deps = resolve_dependencies(workflow, client)
        except DependencyResolutionError as e:
            console.print('[bold red]failed[/bold red]')
            raise PushError(str(e)) from e
        console.print(f'[green]✓[/green] ({len(resolved_deps)} resolved)')

        # Step 4: Generate layout
        console.print('[dim]Generating layout...[/dim]', end=' ')
        layout = generate_node_layout(workflow)
        console.print('[green]✓[/green]')

        # Step 5: Convert to API payload
        org_id = UUID(config.org_id) if config.org_id else org_id_from_lock
        if not org_id:
            raise PushError('Organization ID is required (set via --org or WORKFLOW_ORG_ID)')

        payload = wdf_to_api_payload(workflow, resolved_deps, layout, org_id, workflow_id)

        # Step 6: Call atomic save endpoint
        console.print(f'[dim]Pushing to {config.host}...[/dim]', end=' ')
        try:
            response = client.save_complete_workflow(payload)
        except Exception as e:
            console.print('[bold red]failed[/bold red]')
            raise PushError(f'API call failed: {e}') from e
        console.print('[green]✓[/green]')

        # Step 7: Write lockfile
        console.print('[dim]Writing lockfile...[/dim]', end=' ')
        lock = WorkflowLock(
            workflow_id=UUID(str(response.workflow.id)),
            organization_id=UUID(str(response.workflow.organization_id)),
            version=1,
            instance=config.host or '',
            pushed_at=datetime.now(UTC),
        )

        # Map node slugs to UUIDs from response
        # Match nodes by order (payload order matches response order)
        node_slugs = list(workflow.nodes.keys())
        for idx, node in enumerate(response.nodes):
            if idx < len(node_slugs):
                slug = node_slugs[idx]
                node_uuid = UUID(str(node.id))
                lock.set_node_uuid(slug, node_uuid)

        # Map edge pairs to IDs from response
        # Match edges by order (payload order matches response order)
        for idx, edge in enumerate(response.edges):
            if idx < len(workflow.edges):
                edge_def = workflow.edges[idx]
                # Edge ID might be int or UUID depending on backend
                edge_id = int(edge.id) if not isinstance(edge.id, int) else edge.id
                lock.set_edge_id(edge_def.from_node, edge_def.to, edge_id)

        save_lockfile(file_path, lock)
        console.print(f'[green]✓[/green] {lockfile_path.name}')

    mode = 'Updated' if existing_lock else 'Created'
    console.print(f'[bold green]{mode} workflow:[/bold green] {response.workflow.id}')
