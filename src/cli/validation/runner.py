"""Validation runner that orchestrates all 10 workflow validation checks.

Runs checks in sequence:
1. YAML syntax parsing
2. WDF schema conformance (Pydantic validation)
3-5. Node types, edge refs, entry/exit refs (part of #2)
6. Graph reachability (DFS from entry point)
7. Cycle detection
8. Variable reference validation
9. Node config validation (part of #2)
10. Unsupported node types (document_extraction, etc.)

Returns structured CheckResult objects with PASS/FAIL/WARN status.

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

from cli.wdf_yaml import load_workflow_yaml

# Node types that pass schema validation but are not supported for push/run.
UNSUPPORTED_NODE_TYPES: frozenset[str] = frozenset({'document_extraction'})


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


def run_all_validations(yaml_str: str) -> list[CheckResult]:
    """Run all 10 validation checks on a workflow YAML string.

    Args:
        yaml_str: Raw YAML content of a .workflow.yaml file.

    Returns:
        List of CheckResult objects, one per check. Results are ordered
        by check sequence (YAML -> Schema -> Graph -> Variables).
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

    # Additional synthetic checks for clearer reporting
    # (These are actually covered by WDF Schema Conformance, but we list them separately)
    results.append(CheckResult(check_name='Node Type Recognition', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Edge References', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Entry/Exit Points', status=CheckStatus.PASS))
    results.append(CheckResult(check_name='Node Config Validation', status=CheckStatus.PASS))

    return results
