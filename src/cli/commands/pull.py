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
from workflow_models import (
    LogicalEdgePublic,
    LogicalNodePublic,
    WorkflowMetadataPublic,
    WorkflowPublic,
)
from workflow_models.wdf import EdgeDefinition, NodeDefinition, WorkflowDefinition

from cli.client import WorkflowClient
from cli.config import CLIConfig
from cli.lockfile import WorkflowLock, save_lockfile
from cli.validation.runner import CheckStatus, run_all_validations
from cli.wdf_yaml import dump_workflow_yaml

console = Console()

# UUID regex for matching variable references like {{4a8611ec-ee1e-4d4d-a66e-76ae207d34ee.output.text}}
UUID_REFERENCE_RE = re.compile(
    r'\{\{([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
)

# Regex for matching function_name-based variable references like {{fileUpload_1.output.text}}
# Matches {{ followed by a word (letters, digits, underscores) that is NOT a UUID.
FUNC_NAME_REFERENCE_RE = re.compile(r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)(?=\.)')

# Fields to strip from parameters — frontend-only UI state, not meaningful for WDF
PARAMETERS_UI_FIELDS = frozenset(
    {
        'type',
        'collapsed',
        'validationLevel',
        'validationMessages',
        'function_name',
    }
)


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

    Uses function_name (lowercased) as the primary slug source, preserving
    underscores for compatibility with backend variable references.
    Falls back to slugify(config_type) when function_name is absent.
    Handles collisions by appending _2, _3, etc.

    Args:
        function_name: The node's function_name (may be None or empty).
        config_type: The node's config_type (e.g., 'AGENT', 'LLM_CALL').
        existing_slugs: Set of already-used slugs for collision detection.

    Returns:
        A unique slug string.
    """
    # Determine base slug — use function_name directly (lowercased) to
    # preserve underscores for backend template reference compatibility.
    # Only fall back to slugify() for config_type (which needs normalization).
    if function_name and function_name.strip():
        base = function_name.strip().lower()
    else:
        base = slugify(config_type)

    # If no collision, use the base slug directly
    if base not in existing_slugs:
        return base

    # Handle collisions with numeric suffix
    counter = 2
    while f'{base}_{counter}' in existing_slugs:
        counter += 1
    return f'{base}_{counter}'


# ---------------------------------------------------------------------------
# Parameters -> WDF config extraction
# ---------------------------------------------------------------------------

# Per-node-type field mappings: parameters_key -> wdf_config_key
# Fields are extracted from parameters first, then config as fallback.
# A value of None means the key name is the same in both.
_NODE_TYPE_PARAM_FIELDS: dict[str, dict[str, str | None]] = {
    'PLAIN_TXT_INPUT': {
        # parameters.prompt -> config.placeholder (different naming)
        'prompt': 'placeholder',
    },
    'FILE_UPLOAD': {
        'acceptedFormats': None,
        'maxFileSize': None,
        'saveToMemory': None,
    },
    'AGENT': {
        'agentId': None,
        'model': None,
        'primaryInput': None,
        'temperature': None,
        'maxTokens': None,
        'systemPrompt': 'system_prompt',
        'use_rlm': None,
        'web_tools_enabled': None,
        'max_iterations': None,
        'saveToMemory': None,
        'memoryFilePath': None,
    },
    'RAG_AGENT': {
        'agentId': None,
        'knowledgeBasesOverride': 'knowledgeBaseIds',
        'primaryInput': None,
        'topK': None,
        'systemPrompt': 'system_prompt',
        'saveToMemory': None,
        'memoryFilePath': None,
    },
    'LLM_CALL': {
        'model': None,
        'template': None,
        'system_prompt': None,
        'systemPrompt': 'system_prompt',
        'temperature': None,
        'maxTokens': None,
        'saveToMemory': None,
        'memoryFilePath': None,
    },
    'RETRIEVE': {
        'knowledgeBaseId': None,
        'topK': None,
        'searchQuery': None,
        'scoreThreshold': None,
        'saveToMemory': None,
        'memoryFilePath': None,
    },
    'STRUCTURED_INPUT': {
        # schema lives in config, not parameters
    },
    'STRUCTURED_OUTPUT': {
        # schema lives in config, not parameters
        'primaryInput': None,
        'systemPrompt': 'system_prompt',
        'saveToMemory': None,
        'memoryFilePath': None,
    },
    'HUMAN_REVIEW': {
        'instructions': 'review_prompt',
        'review_prompt': None,
        'timeoutMinutes': None,
        'requireApproval': 'allowApprove',
        'allowDataEditing': 'allowEdit',
    },
    'DOCUMENT_EXTRACTION': {
        'extract_tables': 'extractTables',
        'extract_images': 'extractImages',
    },
}

# Config-only fields (read from node.config when parameters doesn't have them)
_NODE_TYPE_CONFIG_FIELDS: dict[str, dict[str, str | None]] = {
    'PLAIN_TXT_INPUT': {
        'placeholder': None,
    },
    'AGENT': {
        'model': None,
        'model_name': 'model',
        'primaryInput': None,
        'temperature': None,
        'max_tokens': 'maxTokens',
        'maxTokens': None,
        'tools': None,
        'agent_id': 'agentId',
        'use_rlm': None,
        'web_tools_enabled': None,
        'max_iterations': None,
    },
    'STRUCTURED_INPUT': {
        'schema': None,
    },
    'STRUCTURED_OUTPUT': {
        'schema': None,
        'model': None,
    },
    'RAG_AGENT': {
        'agent_id': 'agentId',
        'knowledge_base_ids': 'knowledgeBaseIds',
        'primaryInput': None,
    },
    'RETRIEVE': {
        'enable_reranking': 'enableReranking',
        'include_metadata': 'includeMetadata',
        'knowledge_base_id': 'knowledgeBaseId',
        'knowledge_base_ids': 'knowledgeBaseId',
    },
    'HUMAN_REVIEW': {
        'review_prompt': None,
        'timeout_minutes': 'timeoutMinutes',
        'allow_approve': 'allowApprove',
        'allow_reject': 'allowReject',
        'allow_edit': 'allowEdit',
    },
    'DOCUMENT_EXTRACTION': {
        'fields': None,
        'extractionMethod': None,
        'prompt': None,
    },
    'MEMORY_FILE_URL': {
        'path': None,
    },
}


def _is_empty_ref(value: Any) -> bool:
    """Return True if *value* is an empty dependency reference.

    Dependency-reference fields (``agentId``, ``knowledgeBaseId``,
    ``knowledgeBasesOverride``, etc.) can be stored as ``None``, ``""``,
    or ``[]`` in the database when no agent / knowledge-base is linked.
    These empty sentinels carry no useful information and should be
    omitted from the pulled WDF config so that the YAML stays clean and
    the push path can treat their absence as "not configured".
    """
    return value is None or value == '' or value == []


# Fields that represent dependency references (agent / KB links).
# When these come back as None / '' / [] from the API they should be
# dropped rather than written into the WDF config.
_DEPENDENCY_REF_FIELDS: set[str] = {
    # camelCase (frontend / parameters)
    'agentId',
    'knowledgeBasesOverride',
    'knowledgeBaseId',
    # snake_case (CLI-pushed / config)
    'agent_id',
    'knowledge_base_id',
    'knowledge_base_ids',
}


def extract_node_config(
    config_type: str,
    parameters: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Extract WDF config fields from node parameters and config dicts.

    The backend stores node data in two dicts:
    - ``parameters``: Frontend/UI state + runtime reference IDs
    - ``config``: Structured runtime configuration

    This function merges them into a single WDF-compatible config dict,
    using parameters as the primary source and config as fallback.

    Dependency-reference fields (agentId, knowledgeBaseId, etc.) that are
    ``None``, empty strings, or empty lists are silently dropped — they
    carry no useful information and would produce noisy YAML output.

    Args:
        config_type: Uppercase config type (e.g., 'RAG_AGENT').
        parameters: The node's parameters dict from the API.
        config: The node's config dict from the API.

    Returns:
        A merged config dict suitable for WDF NodeDefinition.config.
    """
    result: dict[str, Any] = {}

    # Step 1: Extract fields from parameters (primary source)
    param_fields = _NODE_TYPE_PARAM_FIELDS.get(config_type, {})
    for param_key, wdf_key in param_fields.items():
        if param_key in parameters:
            value = parameters[param_key]
            target_key = wdf_key if wdf_key is not None else param_key
            # Drop empty dependency references (None / '' / [])
            if param_key in _DEPENDENCY_REF_FIELDS and _is_empty_ref(value):
                continue
            result[target_key] = value

    # Step 2: Fill gaps from config (fallback source)
    config_fields = _NODE_TYPE_CONFIG_FIELDS.get(config_type, {})
    for config_key, wdf_key in config_fields.items():
        target_key = wdf_key if wdf_key is not None else config_key
        # Only fill if not already set from parameters
        if target_key not in result and config_key in config:
            value = config[config_key]
            # Drop empty dependency references (None / '' / [])
            if config_key in _DEPENDENCY_REF_FIELDS and _is_empty_ref(value):
                continue
            result[target_key] = value

    # Step 3: If no typed fields matched at all, fall back to merging
    # config dict directly (for unknown/new node types or CLI-pushed workflows
    # where everything was in config).
    if not result and config:
        # Use config as-is but strip known non-WDF keys
        result = {k: v for k, v in config.items() if k not in PARAMETERS_UI_FIELDS}

    return result


def replace_variable_references(
    config: dict[str, Any],
    uuid_to_slug: dict[UUID, str],
    func_name_to_slug: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Replace variable references in config values with slug-based references.

    Performs two passes on all string values (recursively through dicts/lists):

    1. **UUID references**: ``{{4a8611ec-....output.text}}`` → ``{{fileupload_1.output.text}}``
    2. **function_name references**: ``{{fileUpload_1.output.text}}`` → ``{{fileupload_1.output.text}}``

    Args:
        config: The node config dict to process.
        uuid_to_slug: Mapping from node UUIDs to slug strings.
        func_name_to_slug: Optional mapping from original function_names to slugs.
            Used to normalize mixed-case function_name references.

    Returns:
        A new config dict with all variable references normalized to slugs.
    """
    return _replace_refs_recursive(config, uuid_to_slug, func_name_to_slug or {})


# Keep the old name as an alias for backward compatibility (tests import it)
replace_uuid_references = replace_variable_references


def _replace_refs_recursive(
    value: Any,
    uuid_to_slug: dict[UUID, str],
    func_name_to_slug: dict[str, str],
) -> Any:
    """Recursively replace variable references in any value."""
    if isinstance(value, str):
        text = _replace_uuids_in_string(value, uuid_to_slug)
        text = _normalize_func_name_refs(text, func_name_to_slug)
        return text
    elif isinstance(value, dict):
        return {
            k: _replace_refs_recursive(v, uuid_to_slug, func_name_to_slug) for k, v in value.items()
        }
    elif isinstance(value, list):
        return [_replace_refs_recursive(item, uuid_to_slug, func_name_to_slug) for item in value]
    else:
        return value


def _replace_uuids_in_string(text: str, uuid_to_slug: dict[UUID, str]) -> str:
    """Replace all UUID references in a template string with slugs."""

    def _replacer(match: re.Match) -> str:
        uuid_str = match.group(1)
        try:
            node_uuid = UUID(uuid_str)
            slug = uuid_to_slug.get(node_uuid)
            if slug:
                return '{{' + slug
        except (ValueError, AttributeError):
            pass
        return match.group(0)  # Return original if not found

    return UUID_REFERENCE_RE.sub(_replacer, text)


def _normalize_func_name_refs(text: str, func_name_to_slug: dict[str, str]) -> str:
    """Normalize function_name-based references to match lowercased slugs.

    Replaces ``{{fileUpload_1.output.text}}`` with ``{{fileupload_1.output.text}}``
    when the function_name ``fileUpload_1`` maps to slug ``fileupload_1``.
    """
    if not func_name_to_slug:
        return text

    def _replacer(match: re.Match) -> str:
        func_name = match.group(1)
        slug = func_name_to_slug.get(func_name)
        if slug and slug != func_name:
            return '{{' + slug
        return match.group(0)

    return FUNC_NAME_REFERENCE_RE.sub(_replacer, text)


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
    # Collect all agent and KB UUIDs referenced in node configs and parameters.
    # The backend stores references in two places:
    # - parameters: agentId, knowledgeBasesOverride, knowledgeBaseId (frontend-created)
    # - config: agent_id, knowledge_base_id, knowledge_base_ids (CLI-pushed)
    agent_uuids: set[UUID] = set()
    kb_uuids: set[UUID] = set()

    for node in nodes:
        config = node.config
        parameters = getattr(node, 'parameters', {}) or {}

        # --- Scan parameters (primary source, frontend-created workflows) ---

        # agentId in parameters (AGENT and RAG_AGENT nodes)
        if 'agentId' in parameters:
            try:
                agent_uuids.add(UUID(parameters['agentId']))
            except (ValueError, TypeError):
                pass

        # knowledgeBasesOverride in parameters (RAG_AGENT nodes — list of UUIDs)
        if 'knowledgeBasesOverride' in parameters:
            for kb_id in parameters.get('knowledgeBasesOverride', []):
                try:
                    kb_uuids.add(UUID(kb_id))
                except (ValueError, TypeError):
                    pass

        # knowledgeBaseId in parameters (RETRIEVE nodes — list of UUIDs)
        if 'knowledgeBaseId' in parameters:
            kb_val = parameters['knowledgeBaseId']
            if isinstance(kb_val, list):
                for kb_id in kb_val:
                    try:
                        kb_uuids.add(UUID(kb_id))
                    except (ValueError, TypeError):
                        pass
            else:
                try:
                    kb_uuids.add(UUID(kb_val))
                except (ValueError, TypeError):
                    pass

        # --- Scan config (fallback, CLI-pushed workflows) ---

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

        # Warn about agent UUIDs that could not be resolved
        unresolved_agents = agent_uuids - set(agent_map.keys())
        for agent_uuid in unresolved_agents:
            console.print(
                f'  [yellow]Warning:[/yellow] Agent {agent_uuid} not found '
                f'— it may belong to another organization or have been deleted'
            )

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

        # Warn about KB UUIDs that could not be resolved
        unresolved_kbs = kb_uuids - set(kb_map.keys())
        for kb_uuid in unresolved_kbs:
            console.print(
                f'  [yellow]Warning:[/yellow] Knowledge base {kb_uuid} not found '
                f'— it may belong to another organization or have been deleted'
            )

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
    func_name_to_slug: dict[str, str] = {}
    existing_slugs: set[str] = set()

    for node in nodes:
        node_uuid = UUID(str(node.id))
        fn_name = getattr(node, 'function_name', None)
        config_type = (
            node.config_type.value if hasattr(node.config_type, 'value') else str(node.config_type)
        )

        slug = generate_slug(fn_name, config_type, existing_slugs)
        existing_slugs.add(slug)

        uuid_to_slug[node_uuid] = slug
        slug_to_uuid[slug] = node_uuid
        # Map original function_name to slug for normalizing template references
        if fn_name and fn_name.strip():
            func_name_to_slug[fn_name] = slug

    # --- Step 2: Convert nodes ---
    wdf_nodes: dict[str, NodeDefinition] = {}
    for node in nodes:
        node_uuid = UUID(str(node.id))
        slug = uuid_to_slug[node_uuid]
        config_type = (
            node.config_type.value if hasattr(node.config_type, 'value') else str(node.config_type)
        )
        execution_mode = (
            node.execution_mode.value
            if hasattr(node.execution_mode, 'value')
            else str(node.execution_mode)
        )

        # Extract WDF config from parameters (primary) + config (fallback)
        parameters = getattr(node, 'parameters', {}) or {}
        raw_config = getattr(node, 'config', {}) or {}
        config = extract_node_config(config_type, parameters, raw_config)

        # Replace variable references in template strings:
        # 1. UUID refs: {{4a8611ec-...output.text}} -> {{fileupload_1.output.text}}
        # 2. function_name refs: {{fileUpload_1.output.text}} -> {{fileupload_1.output.text}}
        config = replace_variable_references(config, uuid_to_slug, func_name_to_slug)

        # --- Reverse-resolve agent/KB UUIDs to human-readable names ---

        # Replace agentId UUID with agent_name
        if 'agentId' in config:
            try:
                agent_uuid = UUID(config['agentId'])
                if agent_uuid in agent_map:
                    config['agent_name'] = agent_map[agent_uuid]
                    del config['agentId']
            except (ValueError, TypeError):
                pass

        # Replace agent_id (snake_case, from CLI-pushed config) with agent_name
        if 'agent_id' in config:
            try:
                agent_uuid = UUID(config['agent_id'])
                if agent_uuid in agent_map:
                    config['agent_name'] = agent_map[agent_uuid]
                    del config['agent_id']
            except (ValueError, TypeError):
                pass

        # Replace knowledgeBaseIds with knowledge_base_names (list)
        if 'knowledgeBaseIds' in config:
            resolved_names = []
            unresolved_ids = []
            for kb_id_str in config['knowledgeBaseIds']:
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
                del config['knowledgeBaseIds']
            # If some are unresolved, keep the original list

        # Replace knowledgeBaseId (singular or list, for RETRIEVE) with knowledge_base_name
        if 'knowledgeBaseId' in config:
            kb_val = config['knowledgeBaseId']
            if isinstance(kb_val, list):
                # RETRIEVE nodes store knowledgeBaseId as a list
                resolved_names = []
                unresolved_ids = []
                for kb_id_str in kb_val:
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
                    del config['knowledgeBaseId']
            else:
                try:
                    kb_uuid_val = UUID(kb_val)
                    if kb_uuid_val in kb_map:
                        config['knowledge_base_name'] = kb_map[kb_uuid_val]
                        del config['knowledgeBaseId']
                except (ValueError, TypeError):
                    pass

        # Replace knowledge_base_id (snake_case, from CLI-pushed config) with knowledge_base_name
        if 'knowledge_base_id' in config:
            try:
                kb_uuid_val = UUID(config['knowledge_base_id'])
                if kb_uuid_val in kb_map:
                    config['knowledge_base_name'] = kb_map[kb_uuid_val]
                    del config['knowledge_base_id']
            except (ValueError, TypeError):
                pass

        # Replace knowledge_base_ids (snake_case list, from CLI-pushed config)
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

        # --- Extract label from parameters ---
        label = parameters.get('label') if parameters else None

        # Only include timeout_seconds in WDF if it differs from the default (30)
        timeout_val = getattr(node, 'timeout_seconds', 30)
        timeout_kwarg = timeout_val if timeout_val != 30 else None

        # Build NodeDefinition — use model_construct to bypass validation
        # since agent_name / knowledge_base_name are CLI-only fields
        node_def = NodeDefinition.model_construct(
            type=config_type.lower(),
            execution_mode=execution_mode,
            label=label,
            config=config,
            timeout_seconds=timeout_kwarg,
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

        edge_type = (
            edge.edge_type.value if hasattr(edge.edge_type, 'value') else str(edge.edge_type)
        )
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

        # Step 2: Fetch all workflow data (single composite call)
        console.print('[dim]Fetching workflow data...[/dim]', end=' ')
        try:
            composite = client.get_composite_workflow(workflow_id)
            workflow = WorkflowPublic.model_validate(composite['workflow'])
            metadata = WorkflowMetadataPublic.model_validate(
                composite['workflow'].get('metadata') or {}
            )
            nodes = [LogicalNodePublic.model_validate(n) for n in composite.get('nodes', [])]
            edges = [LogicalEdgePublic.model_validate(e) for e in composite.get('edges', [])]
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

        # Step 6b: Validate pulled YAML (warn but still write -- file already on disk)
        results = run_all_validations(yaml_content)
        failures = [r for r in results if r.status == CheckStatus.FAIL]
        if failures:
            console.print()
            console.print('[yellow]Warning:[/yellow] Pulled workflow has validation issues:')
            for f in failures:
                console.print(f'  [dim]-[/dim] {f.check_name}: {f.message}')
            console.print('[dim]File written anyway.[/dim]')

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
