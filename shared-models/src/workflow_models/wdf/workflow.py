"""WDF top-level WorkflowDefinition model.

Represents the complete contents of a .workflow.yaml file. Validates
structural integrity: entry/exit must reference existing nodes, edges
must reference existing nodes.

Reference: RFC Section 4.1, Jira RAG-945.
See also: backend/scripts/workflow_complete_tests/payloads/ for the
canonical JSON ``workflow`` and ``metadata`` object shapes.
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from workflow_models.wdf.edges import EdgeDefinition
from workflow_models.wdf.nodes import NodeDefinition


class WorkflowDefinition(BaseModel):
    """Top-level model for a .workflow.yaml file.

    Nodes are keyed by slug (human-readable identifier). Entry and exit
    are slug references. Edges reference node slugs via from/to fields.

    ``version`` defaults to 1 for new workflows and should be bumped on
    updates (matching backend behaviour). ``state_schema`` is optional
    and mirrors the backend's workflow.state_schema object.
    """

    name: str
    description: str | None = None
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    state_schema: dict[str, Any] | None = None
    nodes: dict[str, NodeDefinition]
    edges: list[EdgeDefinition]
    entry: str
    exit: str

    @model_validator(mode='after')
    def validate_graph_references(self) -> 'WorkflowDefinition':
        """Validate that entry, exit, and edge endpoints reference existing nodes."""
        node_slugs = set(self.nodes.keys())

        if not node_slugs:
            raise ValueError('Workflow must have at least one node.')

        if self.entry not in node_slugs:
            raise ValueError(
                f'Entry point {self.entry!r} does not reference a defined node. '
                f'Available nodes: {", ".join(sorted(node_slugs))}'
            )

        if self.exit not in node_slugs:
            raise ValueError(
                f'Exit point {self.exit!r} does not reference a defined node. '
                f'Available nodes: {", ".join(sorted(node_slugs))}'
            )

        for i, edge in enumerate(self.edges):
            if edge.from_node not in node_slugs:
                raise ValueError(
                    f'Edge {i} "from" references {edge.from_node!r} '
                    f'which is not a defined node. '
                    f'Available nodes: {", ".join(sorted(node_slugs))}'
                )
            if edge.to not in node_slugs:
                raise ValueError(
                    f'Edge {i} "to" references {edge.to!r} '
                    f'which is not a defined node. '
                    f'Available nodes: {", ".join(sorted(node_slugs))}'
                )

        return self
