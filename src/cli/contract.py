"""Contract validation module for push-time schema checking.

Validates WDF node configs against the registry's ``config_json_schema``
(Pydantic ``model_json_schema()`` output) using JSON Schema Draft 2020-12.

Key responsibilities:
  - Field alias mapping: WDF config field names -> backend schema field names
  - CLI-only field stripping: fields resolved during push (agent_name, etc.)
  - JSON Schema validation via ``jsonschema.Draft202012Validator``
  - Human-friendly error formatting with fix suggestions

Reference: Phase 46 - Push-Time Contract Validation (CTR-01, CTR-02)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema


@dataclass
class ContractError:
    """A single contract validation error for one node field.

    Attributes:
        node_slug: The workflow node slug (e.g., 'my_agent').
        node_type: The registry node type (e.g., 'AGENT').
        field: Dot-delimited field path, or '(root)' for top-level errors.
        message: Human-readable error description from jsonschema.
        suggestion: Optional fix suggestion (e.g., 'Add the required field ...').
    """

    node_slug: str
    node_type: str
    field: str
    message: str
    suggestion: str | None = None


# ---------------------------------------------------------------------------
# CLI-only fields: stripped before validation (not aliased).
# These fields exist in the WDF config but are resolved/removed during push.
# They should never be validated against the backend config schema.
# ---------------------------------------------------------------------------

_CLI_ONLY_FIELDS: dict[str, frozenset[str]] = {
    'AGENT': frozenset({'agent_name', 'agentId', 'primaryInput', 'tools'}),
    'RAG_AGENT': frozenset(
        {'agent_name', 'agentId', 'knowledge_base_names', 'knowledgeBaseIds', 'primaryInput'}
    ),
    'LLM_CALL': frozenset(),
    'RETRIEVE': frozenset(
        {
            'knowledgeBaseId',
            'knowledge_base_name',
            'knowledge_base_names',
            'searchQuery',
            'scoreThreshold',
            'enableReranking',
            'includeMetadata',
        }
    ),
    'STRUCTURED_OUTPUT': frozenset({'primaryInput'}),
    'HUMAN_REVIEW': frozenset({'review_prompt'}),
}


# ---------------------------------------------------------------------------
# Alias mapping: WDF config field name -> backend schema field name.
# Applied after CLI-only field stripping.
# ---------------------------------------------------------------------------

_WDF_TO_SCHEMA_ALIASES: dict[str, dict[str, str]] = {
    'AGENT': {
        'model': 'model_name',
        'maxTokens': 'max_tokens',
    },
    'RAG_AGENT': {
        'model': 'model_name',
        'maxTokens': 'max_tokens',
        'topK': 'top_k_results',
    },
    'LLM_CALL': {
        'model': 'model_name',
        'maxTokens': 'max_tokens',
        'template': 'system_prompt',
    },
    'RETRIEVE': {
        'topK': 'top_k_results',
        'scoreThreshold': 'similarity_threshold',
        'enableReranking': 'enable_reranking',
        'includeMetadata': 'include_metadata',
    },
    'STRUCTURED_OUTPUT': {
        'model': 'model_name',
        'maxTokens': 'max_tokens',
        'schema': 'output_schema',
    },
    'HUMAN_REVIEW': {
        'timeoutMinutes': 'timeout_minutes',
        'allowApprove': 'allow_approve',
        'allowReject': 'allow_reject',
        'allowEdit': 'allow_edit',
    },
}


def _transform_config(node_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Transform WDF config field names to backend schema field names.

    1. Strips CLI-only fields (resolved during push, not in backend schema).
    2. Applies per-node-type alias mapping (WDF name -> backend name).

    Args:
        node_type: Registry node type string (case-insensitive).
        config: Raw WDF config dict from the workflow definition.

    Returns:
        Transformed config dict ready for JSON Schema validation.
    """
    upper_type = node_type.upper()
    cli_only = _CLI_ONLY_FIELDS.get(upper_type, frozenset())
    aliases = _WDF_TO_SCHEMA_ALIASES.get(upper_type, {})
    transformed: dict[str, Any] = {}
    for key, value in config.items():
        if key in cli_only:
            continue
        new_key = aliases.get(key, key)
        transformed[new_key] = value
    return transformed


def _merge_schema_defs(
    schema: dict[str, Any],
    schema_definitions: dict[str, Any],
) -> dict[str, Any]:
    """Merge shared schema_definitions into a schema's $defs.

    Forward-compatible: when the backend starts hoisting $defs to
    ``registry['schema_definitions']``, this merges them back so
    $ref resolution works correctly.

    Existing $defs in the schema take precedence (no overwrite).

    Args:
        schema: A single node type's ``config_json_schema``.
        schema_definitions: The registry's top-level ``schema_definitions``.

    Returns:
        The schema with merged $defs (or the original if no defs to merge).
    """
    if not schema_definitions:
        return schema
    merged = dict(schema)
    existing_defs = merged.get('$defs', {})
    merged['$defs'] = {**schema_definitions, **existing_defs}
    return merged


def _suggest_fix(error: jsonschema.ValidationError, node_type: str) -> str | None:
    """Return a human-readable fix suggestion based on the validation error.

    Args:
        error: A jsonschema ValidationError instance.
        node_type: The registry node type for context in the suggestion.

    Returns:
        A fix suggestion string, or None if no specific suggestion is available.
    """
    if error.validator == 'required':
        missing = error.message.split("'")[1] if "'" in error.message else 'unknown'
        return f'Add the required field {missing!r} to your {node_type} config'
    if error.validator == 'type':
        expected = error.schema.get('type', 'unknown')
        return f'Change the value to type {expected!r}'
    if error.validator == 'anyOf':
        types = []
        for sub in error.schema.get('anyOf', []):
            if 'type' in sub:
                types.append(sub['type'])
        if types:
            return f'Expected {" or ".join(types)}'
    return None


def _build_schema_lookup(registry: dict) -> dict[str, dict[str, Any]]:
    """Build UPPERCASE node type -> config_json_schema mapping from registry.

    Args:
        registry: The full registry response dict.

    Returns:
        Dict mapping node type names (UPPERCASE) to their JSON Schema objects.
    """
    lookup: dict[str, dict[str, Any]] = {}
    all_types = registry.get('all_node_types', {})
    for type_name, info in all_types.items():
        schema = info.get('config_json_schema')
        if schema:
            lookup[type_name.upper()] = schema
    return lookup


def validate_node_contract(
    node_slug: str,
    node_type: str,
    config: dict[str, Any],
    schema: dict[str, Any],
) -> list[ContractError]:
    """Validate a single node's config against the registry JSON Schema.

    Applies field alias mapping and CLI-only field stripping before validation.

    Args:
        node_slug: The workflow node slug for error attribution.
        node_type: The registry node type (UPPERCASE).
        config: The raw WDF config dict.
        schema: The JSON Schema from ``config_json_schema``.

    Returns:
        List of ContractError objects. Empty list means valid.
    """
    transformed = _transform_config(node_type, config)
    validator = jsonschema.Draft202012Validator(schema)
    errors: list[ContractError] = []
    for error in sorted(validator.iter_errors(transformed), key=lambda e: list(e.path)):
        errors.append(
            ContractError(
                node_slug=node_slug,
                node_type=node_type,
                field='.'.join(str(p) for p in error.absolute_path) or '(root)',
                message=error.message,
                suggestion=_suggest_fix(error, node_type),
            )
        )
    return errors


def validate_contract(
    workflow: Any,
    registry: dict,
) -> list[ContractError]:
    """Validate all workflow nodes against registry config_json_schema.

    Iterates every node in the workflow, looks up its schema from the
    registry, and validates the config. Unknown node types are silently
    skipped (other validation checks handle unknown types).

    Args:
        workflow: Parsed WDF workflow definition (duck-typed: needs .nodes dict).
        registry: Registry response dict with ``all_node_types`` and ``schema_definitions``.

    Returns:
        List of ContractError objects. Empty list means all valid.
    """
    schema_lookup = _build_schema_lookup(registry)
    schema_defs = registry.get('schema_definitions', {})
    all_errors: list[ContractError] = []

    for slug, node_def in workflow.nodes.items():
        registry_type = node_def.type.upper()
        schema = schema_lookup.get(registry_type)
        if schema is None:
            continue

        resolved_schema = _merge_schema_defs(schema, schema_defs)
        errors = validate_node_contract(
            node_slug=slug,
            node_type=registry_type,
            config=node_def.config,
            schema=resolved_schema,
        )
        all_errors.extend(errors)

    return all_errors


def format_contract_errors(errors: list[ContractError]) -> str:
    """Format contract validation errors for CLI display.

    Args:
        errors: List of ContractError objects to format.

    Returns:
        Multi-line formatted string with error details and fix suggestions.
    """
    lines: list[str] = []
    for error in errors:
        field_display = f'.{error.field}' if error.field != '(root)' else ''
        lines.append(f'  {error.node_slug} ({error.node_type}){field_display}: {error.message}')
        if error.suggestion:
            lines.append(f'    Fix: {error.suggestion}')
    return '\n'.join(lines)
