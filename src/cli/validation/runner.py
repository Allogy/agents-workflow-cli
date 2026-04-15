"""Validation runner that orchestrates all 13 workflow validation checks.

Runs checks in sequence:
1. YAML syntax parsing
2. WDF schema conformance (Pydantic validation)
3-5. Node types, edge refs, entry/exit refs (part of #2)
6. Graph reachability (DFS from entry point)
7. Cycle detection
8. Variable reference validation
9. Node config validation (part of #2)
10. Unsupported node types (document_extraction, etc.)
11. Output variable paths (registry-powered)
12. Inactive node types (registry-powered)
13. Field coverage drift (registry-powered)

Returns structured CheckResult objects with PASS/FAIL/WARN/SKIP status.

Reference: Jira RAG-947
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import yaml
from pydantic import ValidationError
from workflow_models.wdf.validation import (
    check_cycles,
    check_reachability,
    check_variable_references,
)
from workflow_models.wdf.nodes import NODE_TYPE_CONFIG_MAP
from workflow_models.wdf.variable_ref import extract_variable_refs
from workflow_models.wdf.workflow import WorkflowDefinition

from cli.wdf_yaml import load_workflow_yaml

# Node types that pass schema validation but are not supported for push/run.
UNSUPPORTED_NODE_TYPES: frozenset[str] = frozenset({'document_extraction'})

# Node types where all output paths are user-defined (skip output path validation).
_DYNAMIC_OUTPUT_TYPES: frozenset[str] = frozenset({'STRUCTURED_INPUT'})

# Base fields present on every registry node type -- excluded from field coverage comparison.
_REGISTRY_BASE_FIELDS: frozenset[str] = frozenset({'name', 'description', 'metadata'})


class CheckStatus(str, Enum):
    """Status of a validation check."""

    PASS = 'PASS'
    FAIL = 'FAIL'
    WARN = 'WARN'
    SKIP = 'SKIP'  # Registry checks skipped (offline, no cache, --offline flag)


@dataclass
class CheckResult:
    """Result of a single validation check.

    Attributes:
        check_name: Human-readable name of the check.
        status: PASS, FAIL, or WARN.
        message: Message describing the result (None for PASS).
        details: Optional additional information (e.g., line numbers, node names).
    """

    check_name: str
    status: CheckStatus
    message: str | None = None
    details: dict[str, str | list[str]] | None = None


def run_all_validations(yaml_str: str, *, registry: dict | None = None) -> list[CheckResult]:
    """Run all 13 validation checks on a workflow YAML string.

    Args:
        yaml_str: Raw YAML content of a .workflow.yaml file.
        registry: Optional registry dict with ``all_node_types`` data.
            When *None*, the Output Variable Paths check is skipped.

    Returns:
        List of CheckResult objects, one per check. Results are ordered
        by check sequence (YAML -> Schema -> Graph -> Variables -> Output Paths).
    """
    results: list[CheckResult] = []

    # Check 1: YAML syntax
    try:
        yaml.safe_load(yaml_str)
        results.append(CheckResult(check_name='YAML Syntax', status=CheckStatus.PASS))
    except yaml.YAMLError as e:
        # Extract line/column info if available
        problem_mark = getattr(e, 'problem_mark', None)
        if problem_mark:
            msg = f'YAML parsing error at line {problem_mark.line + 1}, column {problem_mark.column + 1}: {e.problem}'
        else:
            msg = f'YAML parsing error: {e!s}'
        results.append(CheckResult(check_name='YAML Syntax', status=CheckStatus.FAIL, message=msg))
        # Can't continue if YAML is invalid
        return results

    # Checks 2-5, 9: WDF schema conformance (includes Pydantic validation)
    # This covers:
    # - Node type recognition
    # - Edge from/to reference existing nodes
    # - Entry/exit reference existing nodes
    # - Node config validation per type
    try:
        workflow = load_workflow_yaml(yaml_str)
        results.append(CheckResult(check_name='WDF Schema Conformance', status=CheckStatus.PASS))
    except ValidationError as e:
        # Format Pydantic errors cleanly
        error_lines = []
        for error in e.errors():
            loc = ' -> '.join(str(location) for location in error['loc'])
            error_lines.append(f'{loc}: {error["msg"]}')
        msg = 'Schema validation failed:\n' + '\n'.join(error_lines)
        results.append(
            CheckResult(
                check_name='WDF Schema Conformance',
                status=CheckStatus.FAIL,
                message=msg,
                details={'error_count': str(len(e.errors()))},
            )
        )
        # Can't continue if schema is invalid
        return results

    # Check 6: Graph reachability
    reachability_result = check_reachability(workflow)
    if reachability_result.passed:
        results.append(CheckResult(check_name='Graph Reachability', status=CheckStatus.PASS))
    else:
        # Unreachable nodes are a blocking error (must match server-side validation)
        results.append(
            CheckResult(
                check_name='Graph Reachability',
                status=CheckStatus.FAIL,
                message=reachability_result.message,
                details=reachability_result.details,
            )
        )

    # Check 7: Cycle detection
    cycle_result = check_cycles(workflow)
    if cycle_result.passed:
        results.append(CheckResult(check_name='Cycle Detection', status=CheckStatus.PASS))
    else:
        results.append(
            CheckResult(
                check_name='Cycle Detection',
                status=CheckStatus.FAIL,
                message=cycle_result.message,
                details=cycle_result.details,
            )
        )

    # Check 8: Variable reference validation
    var_ref_result = check_variable_references(workflow)
    if var_ref_result.passed:
        results.append(CheckResult(check_name='Variable References', status=CheckStatus.PASS))
    else:
        results.append(
            CheckResult(
                check_name='Variable References',
                status=CheckStatus.FAIL,
                message=var_ref_result.message,
                details=var_ref_result.details,
            )
        )

    # Check 10: Unsupported node types
    unsupported_found: list[str] = []
    for slug, node in workflow.nodes.items():
        if node.type in UNSUPPORTED_NODE_TYPES:
            unsupported_found.append(f'{slug} ({node.type})')
    if unsupported_found:
        results.append(
            CheckResult(
                check_name='Unsupported Node Types',
                status=CheckStatus.FAIL,
                message=f'Unsupported node types found: {", ".join(unsupported_found)}',
                details={'unsupported_nodes': unsupported_found},
            )
        )
    else:
        results.append(CheckResult(check_name='Unsupported Node Types', status=CheckStatus.PASS))

    # Check 11: Output Variable Paths (registry-powered)
    results.append(check_output_variable_paths(workflow, registry))

    # Check 12: Inactive Node Types (registry-powered)
    results.append(check_inactive_node_types(workflow, registry))

    # Check 13: Field Coverage (registry-powered)
    results.append(check_field_coverage(workflow, registry))

    # Additional synthetic checks for clearer reporting
    # (These are actually covered by WDF Schema Conformance, but we list them separately)
    results.append(CheckResult(check_name='Node Type Recognition', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Edge References', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Entry/Exit Points', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Node Config Validation', status=CheckStatus.PASS))

    return results


def _build_output_var_lookup(registry: dict) -> dict[str, set[str]]:
    """Build node_type (UPPERCASE) -> set of known output paths from registry."""
    lookup: dict[str, set[str]] = {}
    for node_info in registry.get('all_node_types', []):
        node_type = node_info.get('type', '')
        paths = {
            var.get('path', '') for var in node_info.get('output_variables', []) if var.get('path')
        }
        if paths:
            lookup[node_type] = paths
    return lookup


def check_output_variable_paths(
    workflow: WorkflowDefinition,
    registry: dict | None,
) -> CheckResult:
    """Validate output variable field paths against registry's known output_variables.

    For each ``{{slug.output.field}}`` reference:
    1. Resolve slug -> node -> node.type
    2. Look up node type's known output_variables from registry
    3. Check if the referenced path is known

    Returns SKIP if registry is None (offline/no cache).
    Returns PASS if all paths are valid.
    Returns FAIL if any path references an unknown output variable.
    """
    if registry is None:
        return CheckResult(
            check_name='Output Variable Paths',
            status=CheckStatus.SKIP,
            message='Registry unavailable -- skipping output variable path check',
        )

    lookup = _build_output_var_lookup(registry)
    invalid_refs: list[dict[str, str]] = []

    for slug, node_def in workflow.nodes.items():
        refs = extract_variable_refs(node_def.config)
        for ref in refs:
            # Skip refs to non-existent nodes (caught by Check 8)
            if ref.slug not in workflow.nodes:
                continue

            referenced_node = workflow.nodes[ref.slug]
            registry_type = referenced_node.type.upper()

            # Skip node types with fully dynamic output
            if registry_type in _DYNAMIC_OUTPUT_TYPES:
                continue

            known_paths = lookup.get(registry_type)
            if known_paths is None:
                # Node type not in registry -- skip (not an error)
                continue

            if ref.path not in known_paths:
                valid_alternatives = sorted(known_paths)
                invalid_refs.append(
                    {
                        'node': slug,
                        'referenced_node': ref.slug,
                        'referenced_type': registry_type,
                        'path': ref.path,
                        'reference': f'{{{{{ref.raw}}}}}',
                        'valid_paths': ', '.join(valid_alternatives),
                    }
                )

    if invalid_refs:
        details_lines = []
        for inv in invalid_refs:
            details_lines.append(
                f'  {inv["reference"]} in node {inv["node"]!r}: '
                f'{inv["path"]!r} is not a known output of {inv["referenced_type"]}. '
                f'Valid: {inv["valid_paths"]}'
            )
        return CheckResult(
            check_name='Output Variable Paths',
            status=CheckStatus.FAIL,
            message='Unknown output variable paths:\n' + '\n'.join(details_lines),
            details={'invalid_refs': invalid_refs},
        )

    return CheckResult(
        check_name='Output Variable Paths',
        status=CheckStatus.PASS,
    )


def _build_status_lookup(registry: dict) -> dict[str, str]:
    """Build node_type (UPPERCASE) -> status string from registry."""
    return {
        info.get('type', ''): info.get('status', 'active')
        for info in registry.get('all_node_types', [])
    }


def _build_registry_fields_lookup(registry: dict) -> dict[str, set[str]]:
    """Build node_type (UPPERCASE) -> set of field names from registry."""
    lookup: dict[str, set[str]] = {}
    for info in registry.get('all_node_types', []):
        node_type = info.get('type', '')
        fields = {f.get('name', '') for f in info.get('fields', []) if f.get('name')}
        if fields:
            lookup[node_type] = fields
    return lookup


def check_inactive_node_types(
    workflow: WorkflowDefinition,
    registry: dict | None,
) -> CheckResult:
    """Check whether any workflow nodes use inactive node types per the registry.

    Returns SKIP if registry is None (offline/no cache).
    Returns PASS if all used node types are active.
    Returns WARN if any node uses an inactive type.
    """
    if registry is None:
        return CheckResult(
            check_name='Inactive Node Types',
            status=CheckStatus.SKIP,
            message='Registry unavailable -- skipping inactive node type check',
        )

    status_lookup = _build_status_lookup(registry)
    inactive_found: list[str] = []

    for slug, node_def in workflow.nodes.items():
        registry_type = node_def.type.upper()
        status = status_lookup.get(registry_type)
        if status == 'inactive':
            inactive_found.append(f'{slug} ({registry_type})')

    if inactive_found:
        return CheckResult(
            check_name='Inactive Node Types',
            status=CheckStatus.WARN,
            message=f'Inactive node types: {", ".join(inactive_found)}',
            details={'inactive_nodes': inactive_found},
        )

    return CheckResult(
        check_name='Inactive Node Types',
        status=CheckStatus.PASS,
    )


def check_field_coverage(
    workflow: WorkflowDefinition,
    registry: dict | None,
) -> CheckResult:
    """Compare CLI WDF model fields against registry fields per node type.

    Surfaces:
    - CLI-only fields: present in WDF model but not in registry.
    - Registry-only fields: present in registry but not in WDF model
      (base fields like name/description/metadata are excluded).

    Returns SKIP if registry is None (offline/no cache).
    Returns PASS if no drift detected.
    Returns WARN with per-type breakdown if any drift found.
    """
    if registry is None:
        return CheckResult(
            check_name='Field Coverage',
            status=CheckStatus.SKIP,
            message='Registry unavailable -- skipping field coverage check',
        )

    status_lookup = _build_status_lookup(registry)
    registry_fields_lookup = _build_registry_fields_lookup(registry)

    # Collect unique WDF node types from the workflow (deduplicate)
    seen_types: set[str] = set()
    for _slug, node_def in workflow.nodes.items():
        seen_types.add(node_def.type)

    drift_lines: list[str] = []

    for wdf_type in sorted(seen_types):
        registry_type = wdf_type.upper()

        # Skip inactive types
        if status_lookup.get(registry_type) == 'inactive':
            continue

        # Get WDF fields from the config model
        config_cls = NODE_TYPE_CONFIG_MAP.get(wdf_type)
        if config_cls is None:
            continue
        wdf_fields = set(config_cls.model_fields.keys())

        # Get registry fields (minus base fields)
        reg_fields_raw = registry_fields_lookup.get(registry_type)
        if reg_fields_raw is None:
            continue
        reg_fields = reg_fields_raw - _REGISTRY_BASE_FIELDS

        cli_only = wdf_fields - reg_fields
        reg_only = reg_fields - wdf_fields

        if cli_only or reg_only:
            parts: list[str] = []
            if cli_only:
                parts.append(f'CLI-only: {", ".join(sorted(cli_only))}')
            if reg_only:
                parts.append(f'Registry-only: {", ".join(sorted(reg_only))}')
            drift_lines.append(f'  {registry_type}: {"; ".join(parts)}')

    if drift_lines:
        return CheckResult(
            check_name='Field Coverage',
            status=CheckStatus.WARN,
            message='Schema drift detected:\n' + '\n'.join(drift_lines),
        )

    return CheckResult(
        check_name='Field Coverage',
        status=CheckStatus.PASS,
    )
