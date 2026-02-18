"""WDF edge definition model.

Represents a directed edge between two nodes in a .workflow.yaml file.
Edges reference nodes by their slug (human-readable key), not by UUID.

The ``type`` field accepts EdgeType enum string values (STATIC, CONDITIONAL,
METADATA, RECURSIVE, MAPPING) and defaults to STATIC when omitted.

Reference: RFC Section 4.3, Jira RAG-945.
See also: backend/scripts/workflow_complete_tests/payloads/ for edge shapes.
"""

from pydantic import BaseModel, Field, field_validator

# Valid edge type strings — matches the EdgeType enum in enums.py.
VALID_EDGE_TYPES = {'STATIC', 'CONDITIONAL', 'METADATA', 'RECURSIVE', 'MAPPING'}


class EdgeDefinition(BaseModel):
    """An edge connecting two nodes in a workflow definition.

    In YAML, the source node key is 'from' (a Python reserved word),
    so we use the alias 'from' with the field name 'from_node'.

    The ``type`` field defaults to 'STATIC' to match backend behavior
    where all edges are STATIC unless otherwise specified.
    """

    from_node: str = Field(..., alias='from')
    to: str
    type: str = 'STATIC'
    condition: str | None = None

    model_config = {'populate_by_name': True}

    @field_validator('type')
    @classmethod
    def validate_edge_type(cls, v: str) -> str:
        if v not in VALID_EDGE_TYPES:
            raise ValueError(
                f'Unknown edge type: {v!r}. Valid types: {", ".join(sorted(VALID_EDGE_TYPES))}'
            )
        return v
