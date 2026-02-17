"""
Pydantic v2 schemas for workflow data contracts.

These schemas are pure Pydantic models with zero SQLAlchemy or SQLModel
dependencies. They represent the Create, Update, and Public variants of
workflow entities.
"""

from workflow_models.schemas.edges import (
    LogicalEdgeCreate,
    LogicalEdgePublic,
    LogicalEdgeUpdate,
)
from workflow_models.schemas.execution import (
    WorkflowExecutionCreate,
    WorkflowExecutionPublic,
    WorkflowExecutionUpdate,
)
from workflow_models.schemas.metadata import (
    WorkflowMetadataCreate,
    WorkflowMetadataPublic,
    WorkflowMetadataUpdate,
)
from workflow_models.schemas.nodes import (
    LogicalNodeCreate,
    LogicalNodeInputCreate,
    LogicalNodeInputPublic,
    LogicalNodeOutputCreate,
    LogicalNodeOutputPublic,
    LogicalNodePublic,
    LogicalNodeUpdate,
)
from workflow_models.schemas.visuals import (
    EdgeVisualsCreate,
    EdgeVisualsPublic,
    EdgeVisualsUpdate,
    NodeVisualsCreate,
    NodeVisualsPublic,
    NodeVisualsUpdate,
    WorkflowVisualsCreate,
    WorkflowVisualsPublic,
    WorkflowVisualsUpdate,
)
from workflow_models.schemas.workflows import (
    WorkflowCreate,
    WorkflowPublic,
    WorkflowUpdate,
)

__all__ = [
    # Workflows
    'WorkflowCreate',
    'WorkflowUpdate',
    'WorkflowPublic',
    # Nodes
    'LogicalNodeCreate',
    'LogicalNodeUpdate',
    'LogicalNodePublic',
    'LogicalNodeInputCreate',
    'LogicalNodeInputPublic',
    'LogicalNodeOutputCreate',
    'LogicalNodeOutputPublic',
    # Edges
    'LogicalEdgeCreate',
    'LogicalEdgeUpdate',
    'LogicalEdgePublic',
    # Visuals
    'WorkflowVisualsCreate',
    'WorkflowVisualsUpdate',
    'WorkflowVisualsPublic',
    'NodeVisualsCreate',
    'NodeVisualsUpdate',
    'NodeVisualsPublic',
    'EdgeVisualsCreate',
    'EdgeVisualsUpdate',
    'EdgeVisualsPublic',
    # Metadata
    'WorkflowMetadataCreate',
    'WorkflowMetadataUpdate',
    'WorkflowMetadataPublic',
    # Execution
    'WorkflowExecutionCreate',
    'WorkflowExecutionUpdate',
    'WorkflowExecutionPublic',
]
