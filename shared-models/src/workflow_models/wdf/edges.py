"""WDF edge definition model.

Represents a directed edge between two nodes in a .workflow.yaml file.
Edges reference nodes by their slug (human-readable key), not by UUID.

Reference: RFC Section 4.3, Jira RAG-945.
"""

from pydantic import BaseModel, Field


class EdgeDefinition(BaseModel):
    """An edge connecting two nodes in a workflow definition.

    In YAML, the source node key is 'from' (a Python reserved word),
    so we use the alias 'from' with the field name 'from_node'.
    """

    from_node: str = Field(..., alias='from')
    to: str
    type: str | None = None
    condition: str | None = None

    model_config = {'populate_by_name': True}
