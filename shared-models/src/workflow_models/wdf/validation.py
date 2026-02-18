"""Graph validation functions for WDF workflows.

Pure functions for offline validation of workflow definitions:
- Reachability analysis (DFS from entry point)
- Cycle detection (DFS with 3-color marking)
- Variable reference validation ({{slug.output.field}})

These functions operate on WorkflowDefinition models with no I/O or API calls.
Adapted from backend/src/services/workflow_validation.py.

Reference: Jira RAG-947, RFC Section 4.1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflow_models.wdf.variable_ref import extract_variable_refs
from workflow_models.wdf.workflow import WorkflowDefinition


@dataclass
class ValidationResult:
    """Result of a single validation check.

    Attributes:
        passed: Whether the check passed (True) or failed (False).
        message: Human-readable message describing the result.
        details: Optional dict with additional information (e.g., unreachable nodes).
    """

    passed: bool
    message: str
    details: dict[str, Any] | None = None


def check_reachability(workflow: WorkflowDefinition) -> ValidationResult:
    """Check that all nodes are reachable from the entry point via DFS.

    Args:
        workflow: The workflow definition to validate.

    Returns:
        ValidationResult with passed=True if all nodes reachable, False otherwise.
        For failures, details['unreachable_nodes'] contains a sorted list of slugs.
    """
    node_slugs = set(workflow.nodes.keys())

    # Build adjacency list from edges
    adjacency: dict[str, list[str]] = {slug: [] for slug in node_slugs}
    for edge in workflow.edges:
        if edge.from_node in adjacency:
            adjacency[edge.from_node].append(edge.to)

    # DFS from entry point
    reachable = _dfs_reachable(workflow.entry, adjacency)

    # Check for unreachable nodes
    unreachable = sorted(node_slugs - reachable)

    if unreachable:
        return ValidationResult(
            passed=False,
            message=f'Unreachable nodes from entry point: {", ".join(unreachable)}',
            details={'unreachable_nodes': unreachable},
        )

    return ValidationResult(
        passed=True,
        message='All nodes are reachable from entry point',
    )


def _dfs_reachable(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    """Perform DFS from start node and return all reachable nodes."""
    reachable = set()
    stack = [start]
    visited = {start}

    while stack:
        current = stack.pop()
        reachable.add(current)

        for neighbor in adjacency.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                stack.append(neighbor)

    return reachable


def check_cycles(workflow: WorkflowDefinition) -> ValidationResult:
    """Check for cycles in the workflow graph using DFS 3-color marking.

    RECURSIVE edge types are excluded from cycle detection as they are intentionally
    allowed to create loops.

    Args:
        workflow: The workflow definition to validate.

    Returns:
        ValidationResult with passed=True if no cycles, False if cycles detected.
        For failures, details['cycle_path'] contains the cycle (if found).
    """
    node_slugs = set(workflow.nodes.keys())

    # Build adjacency list, excluding RECURSIVE edges
    adjacency: dict[str, list[str]] = {slug: [] for slug in node_slugs}
    for edge in workflow.edges:
        if edge.type != 'RECURSIVE' and edge.from_node in adjacency:
            adjacency[edge.from_node].append(edge.to)

    # DFS with 3-color marking (WHITE=0, GRAY=1, BLACK=2)
    # GRAY = currently being explored (in DFS stack), back edge = cycle
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {slug: WHITE for slug in node_slugs}
    parent = {}

    def dfs(node: str) -> str | None:
        """DFS that returns the node where cycle is detected, or None."""
        color[node] = GRAY

        for neighbor in adjacency.get(node, []):
            if color[neighbor] == GRAY:
                # Back edge detected — cycle found
                # Reconstruct cycle path
                cycle = [neighbor]
                current = node
                while current != neighbor:
                    cycle.append(current)
                    current = parent.get(current)
                    if current is None:  # Safety: shouldn't happen
                        break
                cycle.append(neighbor)
                cycle.reverse()
                return ' -> '.join(cycle)

            if color[neighbor] == WHITE:
                parent[neighbor] = node
                cycle_path = dfs(neighbor)
                if cycle_path:
                    return cycle_path

        color[node] = BLACK
        return None

    # Check all nodes (to handle disconnected components)
    for slug in node_slugs:
        if color[slug] == WHITE:
            cycle_path = dfs(slug)
            if cycle_path:
                return ValidationResult(
                    passed=False,
                    message=f'Cycle detected: {cycle_path}',
                    details={'cycle_path': cycle_path},
                )

    return ValidationResult(
        passed=True,
        message='No cycles detected',
    )


def check_variable_references(workflow: WorkflowDefinition) -> ValidationResult:
    """Validate that all {{slug.output.field}} references point to existing nodes.

    Args:
        workflow: The workflow definition to validate.

    Returns:
        ValidationResult with passed=True if all refs valid, False otherwise.
        For failures, details['invalid_references'] contains list of invalid slugs.
    """
    node_slugs = set(workflow.nodes.keys())
    invalid_refs = []

    # Extract all variable references from all node configs
    for slug, node_def in workflow.nodes.items():
        refs = extract_variable_refs(node_def.config)
        for ref in refs:
            if ref.slug not in node_slugs:
                invalid_refs.append(
                    {
                        'node': slug,
                        'reference': f'{{{{{ref.raw}}}}}',
                        'invalid_slug': ref.slug,
                    }
                )

    if invalid_refs:
        # Deduplicate by (node, invalid_slug) for cleaner message
        unique_invalid = {}
        for ref in invalid_refs:
            key = (ref['node'], ref['invalid_slug'])
            if key not in unique_invalid:
                unique_invalid[key] = ref

        invalid_list = list(unique_invalid.values())
        invalid_slugs = sorted({ref['invalid_slug'] for ref in invalid_list})

        return ValidationResult(
            passed=False,
            message=(
                f'Invalid variable references to non-existent nodes: {", ".join(invalid_slugs)}'
            ),
            details={'invalid_references': invalid_list},
        )

    return ValidationResult(
        passed=True,
        message='All variable references are valid',
    )
