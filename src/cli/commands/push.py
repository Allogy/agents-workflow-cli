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

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from rich.console import Console
from workflow_models.wdf import WorkflowDefinition
from workflow_models.wdf.nodes import NodeDefinition

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.contract import format_contract_errors, validate_contract
from cli.lockfile import WorkflowLock, get_lockfile_path, load_lockfile, save_lockfile
from cli.registry import get_registry
from cli.validation.runner import run_all_validations
from cli.wdf_yaml import load_workflow_yaml

console = Console()

# Regex for matching slug-based variable references like {{file_upload_1.output.text}}
_SLUG_REFERENCE_RE = re.compile(r'\{\{([a-z0-9][a-z0-9_-]*)')

# Regex for detecting UUID strings (v1-v5 and nil UUID)
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _is_uuid(value: str) -> bool:
    """Check whether a string looks like a UUID.

    Uses a regex check rather than UUID() constructor to avoid false positives
    from non-standard UUID-like strings that UUID() might coerce.

    Args:
        value: String to check.

    Returns:
        True if the string matches the UUID format, False otherwise.
    """
    return bool(_UUID_RE.match(value))


# Maps WDF node type to the ``type`` field value expected in backend parameters
_WDF_TYPE_TO_PARAMS_TYPE: dict[str, str] = {
    'plain_txt_input': 'plainTextInput',
    'structured_input': 'formInput',
    'file_upload': 'fileUpload',
    'agent': 'agent',
    'rag_agent': 'ragAgent',
    'llm_call': 'llmPrompt',
    'structured_output': 'structuredOutput',
    'retrieve': 'retrieve',
    'document_extraction': 'documentExtraction',
    'human_review': 'humanReview',
    'memory_file_url': 'memoryFileUrl',
    'api_consumption': 'apiConsumption',
}


class PushError(Exception):
    """Raised when push operation fails."""


class DependencyResolutionError(PushError):
    """Raised when a dependency (agent/KB) cannot be resolved."""


def _resolve_agent(
    agent_name: str,
    client: WorkflowClient,
    cache: dict[str, UUID],
) -> UUID:
    """Resolve a single agent reference to a UUID.

    If the value is already a UUID, returns it directly.
    Otherwise, checks the cache, then falls back to an API lookup.

    Args:
        agent_name: Agent name or UUID string from the WDF config.
        client: WorkflowClient for API lookups.
        cache: Existing dependency cache (from lockfile).

    Returns:
        The resolved UUID.

    Raises:
        DependencyResolutionError: If the agent cannot be resolved.
    """
    # UUID passthrough: if it looks like a UUID, use as-is
    if _is_uuid(agent_name):
        return UUID(agent_name)

    # Check lockfile cache
    cache_key = f'agent:{agent_name}'
    if cache_key in cache:
        return cache[cache_key]

    # API lookup — fetch the full list once for both matching and error messages
    available = client.list_agents()
    name_lower = agent_name.lower()
    for agent in available:
        if agent.get('name', '').lower() == name_lower:
            return UUID(agent['id'])

    # Build helpful error with available alternatives
    names = [a.get('name', '(unnamed)') for a in available]
    if names:
        names_str = ', '.join(names)
        msg = f"Cannot resolve agent '{agent_name}'. Available agents: {names_str}"
    else:
        msg = f"Cannot resolve agent '{agent_name}'. No agents available in this organization."
    raise DependencyResolutionError(msg)


def _resolve_knowledge_base(
    kb_name: str,
    client: WorkflowClient,
    cache: dict[str, UUID],
) -> UUID:
    """Resolve a single knowledge base reference to a UUID.

    If the value is already a UUID, returns it directly.
    Otherwise, checks the cache, then falls back to an API lookup.

    Args:
        kb_name: Knowledge base name or UUID string from the WDF config.
        client: WorkflowClient for API lookups.
        cache: Existing dependency cache (from lockfile).

    Returns:
        The resolved UUID.

    Raises:
        DependencyResolutionError: If the knowledge base cannot be resolved.
    """
    # UUID passthrough: if it looks like a UUID, use as-is
    if _is_uuid(kb_name):
        return UUID(kb_name)

    # Check lockfile cache
    cache_key = f'kb:{kb_name}'
    if cache_key in cache:
        return cache[cache_key]

    # API lookup — fetch the full list once for both matching and error messages
    available = client.list_knowledge_bases()
    name_lower = kb_name.lower()
    for kb in available:
        if kb.get('name', '').lower() == name_lower:
            return UUID(kb['id'])

    # Build helpful error with available alternatives
    names = [kb.get('name', '(unnamed)') for kb in available]
    if names:
        names_str = ', '.join(names)
        msg = f"Cannot resolve knowledge base '{kb_name}'. Available knowledge bases: {names_str}"
    else:
        msg = (
            f"Cannot resolve knowledge base '{kb_name}'. "
            'No knowledge bases available in this organization.'
        )
    raise DependencyResolutionError(msg)


def resolve_dependencies(
    workflow: WorkflowDefinition,
    client: WorkflowClient,
    existing_lock: WorkflowLock | None = None,
) -> dict[str, UUID]:
    """Resolve agent and KB names to UUIDs.

    Supports three resolution strategies (checked in order):
    1. **UUID passthrough** — if the value looks like a UUID, use as-is.
    2. **Lockfile cache** — if an ``existing_lock`` is provided and contains
       a cached mapping, use the cached UUID without an API call.
    3. **API lookup** — query the platform API to resolve by name.

    Args:
        workflow: The WorkflowDefinition with node configs referencing names.
        client: WorkflowClient for API lookups.
        existing_lock: Optional existing lockfile with cached dependency mappings.

    Returns:
        Dictionary mapping reference names to UUIDs.
        Keys are in the format ``'agent:{name}'`` or ``'kb:{name}'``.

    Raises:
        DependencyResolutionError: If any dependency cannot be resolved.
    """
    resolved: dict[str, UUID] = {}
    cache: dict[str, UUID] = {}
    if existing_lock is not None:
        cache = existing_lock.dependencies or {}

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
                resolved[key] = _resolve_agent(agent_name, client, cache)

        # Check for knowledge_base_name in config
        if 'knowledge_base_name' in config:
            kb_name = config['knowledge_base_name']
            if not isinstance(kb_name, str):
                continue

            key = f'kb:{kb_name}'
            if key not in resolved:
                resolved[key] = _resolve_knowledge_base(kb_name, client, cache)

        # Check for knowledge_base_names (list) in config
        if 'knowledge_base_names' in config:
            for kb_name in config['knowledge_base_names']:
                if not isinstance(kb_name, str):
                    continue
                key = f'kb:{kb_name}'
                if key not in resolved:
                    resolved[key] = _resolve_knowledge_base(kb_name, client, cache)

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


def _replace_slugs_with_uuids(
    text: str,
    slug_to_uuid: dict[str, UUID],
) -> str:
    """Replace slug-based variable references with UUID-based references.

    Converts ``{{file-upload-1.output.text}}`` to
    ``{{4a8611ec-ee1e-4d4d-a66e-76ae207d34ee.output.text}}``.

    Args:
        text: Template string with slug references.
        slug_to_uuid: Mapping from slugs to UUIDs.

    Returns:
        String with slug references replaced by UUID references.
    """

    def _replacer(match: re.Match) -> str:
        slug = match.group(1)
        node_uuid = slug_to_uuid.get(slug)
        if node_uuid:
            return '{{' + str(node_uuid)
        return match.group(0)

    return _SLUG_REFERENCE_RE.sub(_replacer, text)


def build_node_parameters(
    node_def: NodeDefinition,
    node_slug: str,
    node_config: dict[str, Any],
    slug_to_uuid: dict[str, UUID],
) -> dict[str, Any]:
    """Build the backend ``parameters`` dict from WDF node config.

    The backend frontend expects node data in ``parameters`` with specific
    camelCase field names and UI metadata (type, label, function_name, etc.).

    This is the inverse of ``extract_node_config()`` in the pull command.

    Args:
        node_def: The WDF NodeDefinition.
        node_slug: The node's slug key.
        node_config: The already-resolved config dict (agent_name -> agent_id, etc.).
        slug_to_uuid: Mapping from node slugs to UUIDs for variable reference replacement.

    Returns:
        A parameters dict ready for the backend API.
    """
    node_type = node_def.type
    params: dict[str, Any] = {}

    # Common fields
    params_type = _WDF_TYPE_TO_PARAMS_TYPE.get(node_type, node_type)
    params['type'] = params_type
    params['label'] = node_def.label or node_slug
    params['function_name'] = node_slug.replace('-', '_')
    params['collapsed'] = False
    params['validationLevel'] = 'ok'
    params['validationMessages'] = []

    # Node-type-specific fields from config -> parameters
    if node_type == 'plain_txt_input':
        if 'placeholder' in node_config:
            params['prompt'] = node_config['placeholder']
            params['text'] = ''

    elif node_type == 'file_upload':
        for key in ('acceptedFormats', 'maxFileSize', 'saveToMemory'):
            if key in node_config:
                params[key] = node_config[key]

    elif node_type == 'agent':
        # agentId goes in parameters (already resolved from agent_name)
        if 'agent_id' in node_config:
            params['agentId'] = node_config['agent_id']
        for key in ('model', 'temperature', 'maxTokens'):
            if key in node_config:
                params[key] = node_config[key]
        if 'system_prompt' in node_config:
            params['systemPrompt'] = node_config['system_prompt']
        if 'primaryInput' in node_config:
            params['primaryInput'] = _replace_slugs_with_uuids(
                node_config['primaryInput'], slug_to_uuid
            )
        if 'use_rlm' in node_config:
            params['use_rlm'] = node_config['use_rlm']
        if 'web_tools_enabled' in node_config:
            params['web_tools_enabled'] = node_config['web_tools_enabled']
        for key in ('saveToMemory', 'memoryFilePath'):
            if key in node_config:
                params[key] = node_config[key]

    elif node_type == 'rag_agent':
        if 'agent_id' in node_config:
            params['agentId'] = node_config['agent_id']
        # knowledge_base_ids -> knowledgeBasesOverride
        if 'knowledge_base_ids' in node_config:
            params['knowledgeBasesOverride'] = node_config['knowledge_base_ids']
        elif 'knowledge_base_id' in node_config:
            params['knowledgeBasesOverride'] = [node_config['knowledge_base_id']]
        for key in ('model', 'temperature', 'maxTokens'):
            if key in node_config:
                params[key] = node_config[key]
        if 'system_prompt' in node_config:
            params['systemPrompt'] = node_config['system_prompt']
        if 'topK' in node_config:
            params['topK'] = node_config['topK']
        if 'primaryInput' in node_config:
            params['primaryInput'] = _replace_slugs_with_uuids(
                node_config['primaryInput'], slug_to_uuid
            )
        for key in ('saveToMemory', 'memoryFilePath'):
            if key in node_config:
                params[key] = node_config[key]
    elif node_type == 'llm_call':
        for key in ('model', 'temperature', 'maxTokens'):
            if key in node_config:
                params[key] = node_config[key]
        if 'system_prompt' in node_config:
            params['systemPrompt'] = node_config['system_prompt']
        if 'template' in node_config:
            # Replace slug references with UUID references
            params['template'] = _replace_slugs_with_uuids(node_config['template'], slug_to_uuid)
        for key in ('saveToMemory', 'memoryFilePath'):
            if key in node_config:
                params[key] = node_config[key]
        params['variables'] = []

    elif node_type == 'retrieve':
        # Runtime reads knowledge_base_ids (snake_case, plural).
        if 'knowledge_base_id' in node_config:
            params['knowledge_base_ids'] = [node_config['knowledge_base_id']]
        elif 'knowledgeBaseId' in node_config:
            kb_val = node_config['knowledgeBaseId']
            params['knowledge_base_ids'] = kb_val if isinstance(kb_val, list) else [kb_val]
        elif 'knowledge_base_ids' in node_config:
            params['knowledge_base_ids'] = node_config['knowledge_base_ids']
        for key in ('topK', 'scoreThreshold'):
            if key in node_config:
                params[key] = node_config[key]
        # searchQuery may contain variable references — replace slugs with UUIDs
        if 'searchQuery' in node_config:
            params['searchQuery'] = _replace_slugs_with_uuids(
                node_config['searchQuery'], slug_to_uuid
            )
        for key in ('saveToMemory', 'memoryFilePath'):
            if key in node_config:
                params[key] = node_config[key]

    elif node_type == 'human_review':
        # Map WDF field names to the parameter keys the Temporal runtime expects.
        if 'review_prompt' in node_config:
            params['instructions'] = node_config['review_prompt']
        if 'timeoutMinutes' in node_config:
            params['timeoutMinutes'] = node_config['timeoutMinutes']
        if 'allowApprove' in node_config or 'allowReject' in node_config:
            params['requireApproval'] = node_config.get('allowApprove', True)
        if 'allowEdit' in node_config:
            params['allowDataEditing'] = node_config['allowEdit']

    elif node_type == 'document_extraction':
        # Use camelCase to match frontend conventions.
        if 'extractTables' in node_config:
            params['extractTables'] = node_config['extractTables']
        if 'extractImages' in node_config:
            params['extractImages'] = node_config['extractImages']
        if 'fields' in node_config:
            params['fields'] = node_config['fields']
        if 'extractionMethod' in node_config:
            params['extractionMethod'] = node_config['extractionMethod']
        if 'prompt' in node_config:
            params['prompt'] = node_config['prompt']

    elif node_type == 'structured_output':
        # Runtime reads schema from parameters, not config.
        if 'schema' in node_config:
            params['schema'] = node_config['schema']
        for key in ('model', 'temperature', 'maxTokens'):
            if key in node_config:
                params[key] = node_config[key]
        if 'system_prompt' in node_config:
            params['systemPrompt'] = node_config['system_prompt']
        if 'primaryInput' in node_config:
            params['primaryInput'] = _replace_slugs_with_uuids(
                node_config['primaryInput'], slug_to_uuid
            )
        for key in ('saveToMemory', 'memoryFilePath'):
            if key in node_config:
                params[key] = node_config[key]

    elif node_type == 'api_consumption':
        # connectorId references an org-scoped API Connector (resolved upstream).
        if 'connectorId' in node_config:
            params['connectorId'] = node_config['connectorId']
        if 'primaryInput' in node_config:
            params['primaryInput'] = _replace_slugs_with_uuids(
                node_config['primaryInput'], slug_to_uuid
            )
        for key in (
            'maxRecursionDepth',
            'operationHint',
            'timeoutSeconds',
            'saveToMemory',
            'memoryFilePath',
            'responseVariableMappings',
        ):
            if key in node_config:
                params[key] = node_config[key]

    elif node_type == 'structured_input':
        # schema stays in config, not parameters
        pass

    elif node_type == 'memory_file_url':
        # `path` lives in config (registry requires it there). No parameter fields.
        pass

    return params


def wdf_to_api_payload(
    workflow: WorkflowDefinition,
    resolved_deps: dict[str, UUID],
    layout: dict[str, tuple[int, int]],
    org_id: UUID,
    existing_workflow_id: UUID | None = None,
) -> tuple[dict[str, Any], dict[str, UUID]]:
    """Convert WDF model to SaveCompleteWorkflowRequest payload.

    Args:
        workflow: The WorkflowDefinition from YAML.
        resolved_deps: Dependency name -> UUID mappings.
        layout: Node slug -> (x, y) position mappings.
        org_id: Organization ID for the workflow.
        existing_workflow_id: If updating, the existing workflow UUID.

    Returns:
        Tuple of (payload dictionary, slug_to_uuid mapping).
        The payload is ready for POST /v1/workflows/complete.
        The slug_to_uuid dict maps node slugs to their generated UUIDs.
    """
    # Generate UUIDs for all nodes upfront so edges can reference them
    slug_to_uuid: dict[str, UUID] = {}
    for node_slug in workflow.nodes.keys():
        slug_to_uuid[node_slug] = uuid4()

    # Determine entry and exit points
    entry_uuid: UUID | None = None
    exit_uuid: UUID | None = None
    if workflow.entry:
        entry_uuid = slug_to_uuid.get(workflow.entry)
    if workflow.exit:
        exit_uuid = slug_to_uuid.get(workflow.exit)

    # Build the workflow object (no name/description here)
    workflow_obj: dict[str, Any] = {
        'version': workflow.version,
        'organization_id': str(org_id),
        'state_schema': {},
        'execution_config': {},
    }
    if entry_uuid:
        workflow_obj['entry_point'] = str(entry_uuid)
    if exit_uuid:
        workflow_obj['exit_point'] = str(exit_uuid)

    # Build metadata object (name, description, tags)
    metadata: dict[str, Any] = {
        'name': workflow.name,
        'description': workflow.description or '',
        'tags': workflow.tags or [],
        'is_active': True,
        'custom_fields': {},
    }

    # Start building the payload
    payload: dict[str, Any] = {
        'workflow_id': str(existing_workflow_id) if existing_workflow_id else None,
        'workflow': workflow_obj,
        'metadata': metadata,
        'nodes': [],
        'node_inputs': [],
        'node_outputs': [],
        'edges': [],
        'node_visuals': [],
        'edge_visuals': [],
    }

    # Convert nodes
    for node_slug, node_def in workflow.nodes.items():
        node_uuid = slug_to_uuid[node_slug]

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

        # Replace knowledge_base_names (list) with knowledge_base_ids
        if 'knowledge_base_names' in node_config:
            kb_names = node_config.pop('knowledge_base_names')
            kb_ids = []
            for kb_name in kb_names:
                kb_key = f'kb:{kb_name}'
                if kb_key in resolved_deps:
                    kb_ids.append(str(resolved_deps[kb_key]))
            if kb_ids:
                node_config['knowledge_base_ids'] = kb_ids

        # Build parameters dict for the backend frontend
        node_parameters = build_node_parameters(
            node_def,
            node_slug,
            node_config,
            slug_to_uuid,
        )

        # Build the config dict for backend runtime.
        # Most fields live in parameters; config is only used by a few node types.
        runtime_config = {}
        if node_def.type == 'agent':
            if 'tools' in node_config:
                runtime_config['tools'] = node_config['tools']
        elif node_def.type == 'structured_input':
            # Schema-based input nodes keep schema in config for the UI.
            if 'schema' in node_config:
                runtime_config['schema'] = node_config['schema']
        elif node_def.type == 'structured_output':
            # Schema also stays in config for the UI (and is in parameters for runtime).
            if 'schema' in node_config:
                runtime_config['schema'] = node_config['schema']
        elif node_def.type == 'retrieve':
            if 'enableReranking' in node_config:
                runtime_config['enable_reranking'] = node_config['enableReranking']
            if 'includeMetadata' in node_config:
                runtime_config['include_metadata'] = node_config['includeMetadata']
        elif node_def.type == 'memory_file_url':
            # Registry schema requires `path` at the config root. Path may contain
            # `{{slug.output.field}}` refs — replace slugs with node UUIDs so the
            # runtime resolver can find the upstream node.
            if 'path' in node_config:
                runtime_config['path'] = _replace_slugs_with_uuids(
                    node_config['path'], slug_to_uuid
                )
        # human_review, llm_call, rag_agent, file_upload — all data in parameters only

        # Build node payload with correct schema
        node_payload = {
            'id': str(node_uuid),
            'workflow_version': workflow.version,
            'config_type': node_def.type.upper(),  # Backend expects UPPERCASE enum values
            'execution_mode': node_def.execution_mode.upper(),  # Ensure uppercase
            'function_name': node_slug.replace('-', '_'),
            'parameters': node_parameters,
            'retry_policy': {'max_retries': 3},
            'timeout_seconds': node_def.timeout_seconds
            if node_def.timeout_seconds is not None
            else 30,
            'config': runtime_config,
            'delegated_response': False,
            'step_type': 'STEP',
            'join_config': {},
        }
        payload['nodes'].append(node_payload)

        # Generate node outputs based on node type
        # Most nodes have at least one output
        if node_def.type not in ['structured_output']:
            payload['node_outputs'].append(
                {
                    'node_id': str(node_uuid),
                    'output_name': 'output',  # Not 'name'
                    'sequence_order': 0,
                }
            )

        # Generate node inputs based on node type
        # Most nodes (except input nodes) have at least one input
        if node_def.type not in ['plain_txt_input', 'structured_input', 'file_upload']:
            payload['node_inputs'].append(
                {
                    'node_id': str(node_uuid),
                    'input_name': 'input',  # Not 'name'
                    'sequence_order': 0,
                }
            )

        # Add node visuals (position from layout)
        if node_slug in layout:
            x, y = layout[node_slug]
            payload['node_visuals'].append(
                {
                    'node_id': str(node_uuid),
                    'position_x': x,
                    'position_y': y,
                    'width': 180,
                    'height': 80,
                    'style': {},
                    'collapsed': False,
                }
            )

    # Convert edges
    for edge_def in workflow.edges:
        source_uuid = slug_to_uuid.get(edge_def.from_node)
        target_uuid = slug_to_uuid.get(edge_def.to)

        if not source_uuid or not target_uuid:
            raise PushError(f'Edge references unknown node: {edge_def.from_node} -> {edge_def.to}')

        edge_payload = {
            'workflow_version': workflow.version,
            'source_node_id': str(source_uuid),
            'target_node_id': str(target_uuid),
            'edge_type': edge_def.type or 'STATIC',
            'condition_function': None,
            'data_mapping': {},
        }
        payload['edges'].append(edge_payload)

    # Generate a default designer layout from the workflow nodes so the
    # frontend renderer doesn't show "design schema not configured".
    layout_wf_id = str(existing_workflow_id) if existing_workflow_id else str(uuid4())
    payload['metadata']['custom_fields'] = {
        'designer': _generate_designer_layout(layout_wf_id, payload['nodes']),
    }

    return payload, slug_to_uuid


# ---------------------------------------------------------------------------
# Default designer layout generation
# ---------------------------------------------------------------------------
# Maps backend config_type values to (blockType, width, height).
# Sizes match frontend widgetConstants.ts DEFAULT_WIDGET_SIZES.
_NODE_TYPE_WIDGET_MAP: dict[str, tuple[str, int, int]] = {
    'PLAIN_TXT_INPUT': ('input', 8, 2),
    'STRUCTURED_INPUT': ('form', 8, 5),
    'FILE_UPLOAD': ('file_input', 8, 3),
    'LLM_CALL': ('text', 12, 4),
    'AGENT': ('text', 12, 4),
    'RAG_AGENT': ('text', 12, 4),
    'STRUCTURED_OUTPUT': ('text', 12, 4),
    'RETRIEVE': ('text', 12, 4),
    'HUMAN_REVIEW': ('human_review', 8, 5),
}


def _generate_designer_layout(
    workflow_id: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a minimal designer layout from the payload nodes.

    Creates one widget per non-FLOW node, using the correct widget type
    for each node's config_type.  Widgets are stacked vertically and
    centred within a 12-column grid.
    """
    widgets: list[dict[str, Any]] = []
    y = 0

    for idx, node in enumerate(nodes):
        mode = (node.get('execution_mode') or '').upper()
        if mode == 'FLOW':
            continue

        config_type = (node.get('config_type') or '').upper()
        block_type, w, h = _NODE_TYPE_WIDGET_MAP.get(config_type, ('text', 12, 3))
        x = (12 - w) // 2

        widget: dict[str, Any] = {
            'id': str(uuid4()),
            'blockType': block_type,
            'gridItem': {'x': x, 'y': y, 'w': w, 'h': h},
            'blockMapping': {'blockIndex': idx},
        }

        node_id = node.get('id')
        if node_id:
            if block_type == 'form':
                widget['formNodeId'] = str(node_id)
                widget['showSubmitButton'] = True
                widget['runWorkflowOnSubmit'] = True
                widget['submitLabel'] = 'Submit'
            elif block_type in ('input', 'file_input'):
                widget['inputNodeId'] = str(node_id)
                widget['runWorkflowWithButton'] = True
                widget['submitLabel'] = 'Submit'
            elif block_type == 'human_review':
                widget['reviewNodeId'] = str(node_id)

        widgets.append(widget)
        y += h

    return {
        'id': str(uuid4()),
        'workflowId': workflow_id,
        'version': '1.0',
        'gridConfig': {
            'columnCount': 12,
            'rowHeight': 60,
            'margin': 10,
            'float': False,
            'disableOneColumnMode': False,
        },
        'widgets': widgets,
        'updatedAt': datetime.now(UTC).isoformat(),
    }


def push_workflow(
    file_path: Path,
    config: CLIConfig,
    skip_contract_check: bool = False,
) -> None:
    """Push a workflow to the platform.

    Orchestrates the complete push process:
    1. Load and validate YAML
    1b. Contract validation against registry schema (unless skipped)
    2. Check lockfile (create vs. update mode)
    3. Resolve dependencies
    4. Convert WDF to API payload
    5. Call atomic save endpoint
    6. Generate layout
    7. Write/update lockfile

    Args:
        file_path: Path to the .workflow.yaml file.
        config: Resolved CLI configuration.
        skip_contract_check: If True, skip contract validation against registry schemas.

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

    # Step 1b: Contract validation against registry schema
    if not skip_contract_check:
        console.print('[dim]Checking contracts...[/dim]', end=' ')
        registry_result = get_registry(config.host)
        if registry_result is None:
            console.print('[yellow]skipped[/yellow] (registry unavailable)')
        else:
            if registry_result.is_stale:
                console.print('[yellow]using stale cache[/yellow]')
                console.print('[dim]Checking contracts...[/dim]', end=' ')
            contract_errors = validate_contract(workflow, registry_result.registry)
            if contract_errors:
                console.print('[bold red]failed[/bold red]')
                console.print('[red]Contract validation failed:[/red]')
                console.print(format_contract_errors(contract_errors))
                raise PushError(
                    f'Contract validation failed with {len(contract_errors)} error(s). '
                    'Use --skip-contract-check to bypass.'
                )
            console.print('[green]\u2713[/green]')

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
            resolved_deps = resolve_dependencies(workflow, client, existing_lock=existing_lock)
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

        payload, slug_to_uuid = wdf_to_api_payload(
            workflow, resolved_deps, layout, org_id, workflow_id
        )

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
            dependencies=resolved_deps,
            pushed_at=datetime.now(UTC),
        )

        # Map node slugs to UUIDs using our generated mappings
        for slug, node_uuid in slug_to_uuid.items():
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
