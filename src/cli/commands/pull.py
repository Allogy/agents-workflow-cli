"""workflow pull command implementation.

Exports a platform workflow to a local .workflow.yaml + .workflow.lock file pair.

Features:
- Pull by workflow UUID (exact match)
- Pull by workflow name (fuzzy match with interactive selection)
- Custom output path via -o flag
- Reverse-resolve agent/KB UUIDs to human-readable names
- Generate readable node slugs from function_name (fallback: config_type)
- Handle slug collisions with -2, -3 suffixes
- Write lockfile alongside the YAML for round-trip push/pull
- Stripped visual data (node positions, edge paths, canvas viewport)

Usage:
    workflow pull <uuid>
    workflow pull <uuid> -o invoices.workflow.yaml
    workflow pull "Invoice Processing"
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from rich.console import Console
from rich.prompt import Prompt
from workflow_models.wdf import EdgeDefinition, NodeDefinition, WorkflowDefinition

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.lockfile import WorkflowLock, save_lockfile
from cli.wdf_yaml import dump_workflow_yaml

console = Console()


class PullError(Exception):
    """Raised when pull operation fails."""


# ---------------------------------------------------------------------------
# Slug utilities
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert a text string to a URL-friendly slug.

    Rules:
    - Lowercase
    - Replace spaces and underscores with hyphens
    - Remove non-alphanumeric characters (except hyphens)
    - Collapse multiple hyphens into one
    - Strip leading/trailing hyphens

    Args:
        text: The text to slugify.

    Returns:
        A slug string (e.g., 'My Agent Name' -> 'my-agent-name').
    """
    if not text:
        return ''

    # Lowercase
    slug = text.lower()
    # Replace underscores and spaces with hyphens
    slug = slug.replace('_', '-').replace(' ', '-')
    # Remove anything that's not alphanumeric or hyphen
    slug = re.sub(r'[^a-z0-9-]', '', slug)
    # Collapse multiple hyphens
    slug = re.sub(r'-+', '-', slug)
    # Strip leading/trailing hyphens
    slug = slug.strip('-')

    return slug


def generate_slug(
    function_name: str | None,
    config_type: str,
    existing_slugs: set[str],
) -> str:
    """Generate a unique node slug.

    Uses function_name as the primary slug source, falling back to config_type.
    Handles collisions by appending -2, -3, etc.

    Args:
        function_name: The node's function_name (may be None or empty).
        config_type: The node's config_type (e.g., 'AGENT', 'LLM_CALL').
        existing_slugs: Set of already-used slugs for collision detection.

    Returns:
        A unique slug string.
    """
    # Determine base slug
    if function_name and function_name.strip():
        base = slugify(function_name)
    else:
        base = slugify(config_type)

    # If no collision, use the base slug directly
    if base not in existing_slugs:
        return base

    # Handle collisions with numeric suffix
    counter = 2
    while f'{base}-{counter}' in existing_slugs:
        counter += 1
    return f'{base}-{counter}'


# ---------------------------------------------------------------------------
# Reverse resolution: UUIDs -> names
# ---------------------------------------------------------------------------


def reverse_resolve_dependencies(
    nodes: list[Any],
    client: WorkflowClient,
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    """Reverse-resolve agent and KB UUIDs to human-readable names.

    Scans node configs for agent_id and knowledge_base_id fields, then
    looks up the corresponding names via the API.

    Args:
        nodes: List of node objects from the API (with .config and .config_type).
        client: WorkflowClient for API lookups.

    Returns:
        Tuple of (agent_uuid_to_name, kb_uuid_to_name) dictionaries.
        UUIDs not found in the API are simply omitted from the maps.
    """
    # Collect all agent and KB UUIDs referenced in node configs
    agent_uuids: set[UUID] = set()
    kb_uuids: set[UUID] = set()

    for node in nodes:
        config = node.config

        # agent_id field (AGENT nodes)
        if 'agent_id' in config:
            try:
                agent_uuids.add(UUID(config['agent_id']))
            except (ValueError, TypeError):
                pass

        # knowledge_base_id field (RETRIEVE nodes)
        if 'knowledge_base_id' in config:
            try:
                kb_uuids.add(UUID(config['knowledge_base_id']))
            except (ValueError, TypeError):
                pass

        # knowledge_base_ids field (RAG_AGENT nodes — list of UUIDs)
        if 'knowledge_base_ids' in config:
            for kb_id in config.get('knowledge_base_ids', []):
                try:
                    kb_uuids.add(UUID(kb_id))
                except (ValueError, TypeError):
                    pass

    # Build agent UUID -> name map
    agent_map: dict[UUID, str] = {}
    if agent_uuids:
        agents = client.list_agents()
        for agent in agents:
            try:
                agent_uuid = UUID(agent['id'])
                if agent_uuid in agent_uuids:
                    agent_map[agent_uuid] = agent['name']
            except (KeyError, ValueError, TypeError):
                pass

    # Build KB UUID -> name map
    kb_map: dict[UUID, str] = {}
    if kb_uuids:
        knowledge_bases = client.list_knowledge_bases()
        for kb in knowledge_bases:
            try:
                kb_uuid = UUID(kb['id'])
                if kb_uuid in kb_uuids:
                    kb_map[kb_uuid] = kb['name']
            except (KeyError, ValueError, TypeError):
                pass

    return agent_map, kb_map


# ---------------------------------------------------------------------------
# API response -> WDF conversion
# ---------------------------------------------------------------------------


def api_response_to_wdf(
    workflow: Any,
    metadata: Any,
    nodes: list[Any],
    edges: list[Any],
    agent_map: dict[UUID, str],
    kb_map: dict[UUID, str],
) -> tuple[WorkflowDefinition, dict[str, UUID], dict[str, int | UUID]]:
    """Convert API response objects into a WorkflowDefinition.

    This is the inverse of push's ``wdf_to_api_payload``:
    - Generates human-readable slugs from function_name / config_type
    - Replaces agent/KB UUIDs with names in node configs
    - Maps entry/exit UUIDs to slugs
    - Strips visual data (not present in API list responses)

    Args:
        workflow: WorkflowPublic object.
        metadata: WorkflowMetadataPublic object.
        nodes: List of LogicalNodePublic objects.
        edges: List of LogicalEdgePublic objects.
        agent_map: UUID -> agent name mapping.
        kb_map: UUID -> KB name mapping.

    Returns:
        Tuple of (WorkflowDefinition, slug_to_uuid, edge_key_to_id).
        - slug_to_uuid maps node slugs to server UUIDs.
        - edge_key_to_id maps 'source->target' slug pairs to server edge IDs.
    """
    # --- Step 1: Generate slugs for all nodes ---
    uuid_to_slug: dict[UUID, str] = {}
    slug_to_uuid: dict[str, UUID] = {}
    existing_slugs: set[str] = set()

    for node in nodes:
        node_uuid = UUID(str(node.id))
        fn_name = getattr(node, 'function_name', None)
        config_type = (
            node.config_type if isinstance(node.config_type, str) else str(node.config_type)
        )

        slug = generate_slug(fn_name, config_type, existing_slugs)
        existing_slugs.add(slug)

        uuid_to_slug[node_uuid] = slug
        slug_to_uuid[slug] = node_uuid

    # --- Step 2: Convert nodes ---
    wdf_nodes: dict[str, NodeDefinition] = {}
    for node in nodes:
        node_uuid = UUID(str(node.id))
        slug = uuid_to_slug[node_uuid]
        config_type = (
            node.config_type if isinstance(node.config_type, str) else str(node.config_type)
        )
        execution_mode = (
            node.execution_mode
            if isinstance(node.execution_mode, str)
            else str(node.execution_mode)
        )

        # Clone and transform config
        config = dict(node.config)

        # Replace agent_id with agent_name
        if 'agent_id' in config:
            try:
                agent_uuid = UUID(config['agent_id'])
                if agent_uuid in agent_map:
                    config['agent_name'] = agent_map[agent_uuid]
                    del config['agent_id']
            except (ValueError, TypeError):
                pass

        # Replace knowledge_base_id with knowledge_base_name
        if 'knowledge_base_id' in config:
            try:
                kb_uuid_val = UUID(config['knowledge_base_id'])
                if kb_uuid_val in kb_map:
                    config['knowledge_base_name'] = kb_map[kb_uuid_val]
                    del config['knowledge_base_id']
            except (ValueError, TypeError):
                pass

        # Replace knowledge_base_ids with knowledge_base_names (list)
        if 'knowledge_base_ids' in config:
            resolved_names = []
            unresolved_ids = []
            for kb_id_str in config['knowledge_base_ids']:
                try:
                    kb_uuid_val = UUID(kb_id_str)
                    if kb_uuid_val in kb_map:
                        resolved_names.append(kb_map[kb_uuid_val])
                    else:
                        unresolved_ids.append(kb_id_str)
                except (ValueError, TypeError):
                    unresolved_ids.append(kb_id_str)

            if resolved_names and not unresolved_ids:
                config['knowledge_base_names'] = resolved_names
                del config['knowledge_base_ids']
            # If some are unresolved, keep the original list

        # Build NodeDefinition — use model_construct to bypass validation
        # since agent_name / knowledge_base_name are CLI-only fields
        node_def = NodeDefinition.model_construct(
            type=config_type.lower(),
            execution_mode=execution_mode,
            label=None,
            config=config,
        )
        wdf_nodes[slug] = node_def

    # --- Step 3: Convert edges ---
    wdf_edges: list[EdgeDefinition] = []
    edge_key_to_id: dict[str, int | UUID] = {}

    for edge in edges:
        source_uuid = UUID(str(edge.source_node_id))
        target_uuid = UUID(str(edge.target_node_id))

        source_slug = uuid_to_slug.get(source_uuid)
        target_slug = uuid_to_slug.get(target_uuid)

        if not source_slug or not target_slug:
            # Skip edges referencing unknown nodes
            continue

        edge_type = edge.edge_type if isinstance(edge.edge_type, str) else str(edge.edge_type)
        condition = getattr(edge, 'condition_function', None)

        edge_def = EdgeDefinition.model_validate(
            {
                'from': source_slug,
                'to': target_slug,
                'type': edge_type,
                'condition': condition,
            }
        )
        wdf_edges.append(edge_def)

        # Track edge ID for lockfile
        edge_key = f'{source_slug}->{target_slug}'
        edge_key_to_id[edge_key] = edge.id

    # --- Step 4: Determine entry and exit slugs ---
    entry_slug = (
        uuid_to_slug.get(UUID(str(workflow.entry_point)), '') if workflow.entry_point else ''
    )
    exit_slug = uuid_to_slug.get(UUID(str(workflow.exit_point)), '') if workflow.exit_point else ''

    # --- Step 5: Build WorkflowDefinition ---
    wdf = WorkflowDefinition.model_construct(
        name=metadata.name or 'Untitled',
        description=metadata.description,
        version=workflow.version,
        tags=list(metadata.tags) if metadata.tags else [],
        state_schema=workflow.state_schema if workflow.state_schema else None,
        nodes=wdf_nodes,
        edges=wdf_edges,
        entry=entry_slug,
        exit=exit_slug,
    )

    return wdf, slug_to_uuid, edge_key_to_id


# ---------------------------------------------------------------------------
# Workflow name resolution (fuzzy match with interactive selection)
# ---------------------------------------------------------------------------


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def find_workflow_by_name(
    client: WorkflowClient,
    name: str,
    org_id: str,
) -> str:
    """Find a workflow ID by name, with interactive selection for multiple matches.

    Args:
        client: WorkflowClient instance.
        name: Workflow name or partial name to search for.
        org_id: Organization ID to filter by.

    Returns:
        Workflow UUID string.

    Raises:
        PullError: If no workflows found or user cancels selection.
    """
    workflows = client.list_workflows(organization_id=org_id)

    # Fetch metadata for each workflow to get names
    workflows_with_names: list[tuple[Any, str]] = []
    for workflow in workflows:
        try:
            metadata = client.get_metadata(workflow.id)
            if metadata.name:
                workflows_with_names.append((workflow, metadata.name))
        except Exception:
            continue

    # First try exact match (case-insensitive)
    exact_matches = [
        (w, w_name) for w, w_name in workflows_with_names if w_name.lower() == name.lower()
    ]

    if len(exact_matches) == 1:
        return str(exact_matches[0][0].id)

    # Try partial match (case-insensitive)
    partial_matches = [
        (w, w_name) for w, w_name in workflows_with_names if name.lower() in w_name.lower()
    ]

    if len(partial_matches) == 0:
        raise PullError(f'No workflow found matching "{name}"')

    if len(partial_matches) == 1:
        return str(partial_matches[0][0].id)

    # Multiple matches — show numbered list and prompt for selection
    console.print(f'\n[yellow]Multiple workflows match "{name}":[/yellow]')
    for idx, (w, w_name) in enumerate(partial_matches, 1):
        wf_id_short = str(w.id)[:8]
        console.print(f'  [bold]{idx}.[/bold] {w_name} ({wf_id_short}...)')

    choice = Prompt.ask(
        '\nSelect a workflow',
        choices=[str(i) for i in range(1, len(partial_matches) + 1)],
    )

    selected_idx = int(choice) - 1
    return str(partial_matches[selected_idx][0].id)


# ---------------------------------------------------------------------------
# Main pull orchestrator
# ---------------------------------------------------------------------------


def pull_workflow(
    identifier: str,
    config: CLIConfig,
    output_path: Path | None = None,
) -> None:
    """Pull a workflow from the platform to a local YAML file.

    Orchestrates the complete pull process:
    1. Resolve identifier (UUID or name) to a workflow ID
    2. Fetch workflow, nodes, edges, metadata via API
    3. Reverse-resolve agent/KB UUIDs to names
    4. Convert API response to WDF (WorkflowDefinition)
    5. Determine output file path
    6. Write .workflow.yaml
    7. Write .workflow.lock

    Args:
        identifier: Workflow UUID or name to pull.
        config: Resolved CLI configuration.
        output_path: Optional output file path. If None, uses slugified
            workflow name + .workflow.yaml.

    Raises:
        PullError: If any step fails.
    """
    config.validate_for_api()

    with WorkflowClient.from_config(config) as client:
        # Step 1: Resolve identifier to workflow ID
        if is_valid_uuid(identifier):
            workflow_id = identifier
            console.print(f'[bold cyan]Pulling workflow:[/bold cyan] {workflow_id}')
        else:
            console.print(f'[bold cyan]Searching for workflow:[/bold cyan] "{identifier}"')
            try:
                workflow_id = find_workflow_by_name(client, identifier, config.org_id)  # type: ignore[arg-type]
            except PullError:
                raise
            console.print(f'[dim]Found workflow: {workflow_id}[/dim]')

        # Step 2: Fetch all workflow data
        console.print('[dim]Fetching workflow data...[/dim]', end=' ')
        try:
            workflow = client.get_workflow(workflow_id)
            metadata = client.get_metadata(workflow_id)
            nodes = client.list_nodes(workflow_id)
            edges = client.list_edges(workflow_id)
        except Exception as e:
            console.print('[bold red]failed[/bold red]')
            raise PullError(f'Failed to fetch workflow data: {e}') from e
        console.print(f'[green]✓[/green] ({len(nodes)} nodes, {len(edges)} edges)')

        # Step 3: Reverse-resolve agent/KB UUIDs
        console.print('[dim]Resolving dependencies...[/dim]', end=' ')
        agent_map, kb_map = reverse_resolve_dependencies(nodes, client)
        total_resolved = len(agent_map) + len(kb_map)
        console.print(f'[green]✓[/green] ({total_resolved} resolved)')

        # Step 4: Convert to WDF
        console.print('[dim]Converting to WDF...[/dim]', end=' ')
        wdf, slug_to_uuid, edge_key_to_id = api_response_to_wdf(
            workflow=workflow,
            metadata=metadata,
            nodes=nodes,
            edges=edges,
            agent_map=agent_map,
            kb_map=kb_map,
        )
        console.print('[green]✓[/green]')

        # Step 5: Determine output path
        if output_path is None:
            workflow_name = metadata.name or 'workflow'
            filename = slugify(workflow_name) + '.workflow.yaml'
            output_path = Path.cwd() / filename

        # Create parent directories if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 6: Write .workflow.yaml
        console.print(f'[dim]Writing {output_path.name}...[/dim]', end=' ')
        yaml_content = dump_workflow_yaml(wdf)
        output_path.write_text(yaml_content)
        console.print('[green]✓[/green]')

        # Step 7: Write .workflow.lock
        console.print('[dim]Writing lockfile...[/dim]', end=' ')
        lock = WorkflowLock(
            workflow_id=UUID(str(workflow.id)),
            organization_id=UUID(str(workflow.organization_id)),
            version=1,
            instance=config.host or '',
            pushed_at=datetime.now(UTC),
        )

        # Map node slugs to UUIDs
        for slug, node_uuid in slug_to_uuid.items():
            lock.set_node_uuid(slug, node_uuid)

        # Map edge pairs to IDs
        for edge_key, edge_id in edge_key_to_id.items():
            parts = edge_key.split('->')
            if len(parts) == 2:
                # edge_id may be UUID; convert to int if possible, otherwise use hash
                if isinstance(edge_id, int):
                    lock.edges[edge_key] = edge_id
                else:
                    # For UUID edge IDs, use hash to store as int
                    lock.edges[edge_key] = edge_id.int % (2**31)

        save_lockfile(output_path, lock)
        lock_path = output_path.with_suffix('.lock')
        console.print(f'[green]✓[/green] {lock_path.name}')

    console.print(f'[bold green]Pulled workflow:[/bold green] {output_path}')
