"""
Shared workflow models for the Agents Platform.

This package provides Pydantic v2 schemas and enums for workflow data contracts,
shared between the backend API and the workflow CLI. It has zero SQLAlchemy or
SQLModel dependencies.
"""

__version__ = '0.1.0'

from workflow_models.enums import (
    EdgeType,
    ExecutionMode,
    ExecutionStatus,
    NodeConfigType,
    NodeExecutionStatus,
    PathType,
    ReducerType,
    StepExecutionType,
)
from workflow_models.schemas import (
    EdgeVisualsCreate,
    EdgeVisualsPublic,
    EdgeVisualsUpdate,
    LogicalEdgeCreate,
    LogicalEdgePublic,
    LogicalEdgeUpdate,
    LogicalNodeCreate,
    LogicalNodeInputCreate,
    LogicalNodeInputPublic,
    LogicalNodeOutputCreate,
    LogicalNodeOutputPublic,
    LogicalNodePublic,
    LogicalNodeUpdate,
    NodeVisualsCreate,
    NodeVisualsPublic,
    NodeVisualsUpdate,
    WorkflowCreate,
    WorkflowExecutionCreate,
    WorkflowExecutionPublic,
    WorkflowExecutionUpdate,
    WorkflowMetadataCreate,
    WorkflowMetadataPublic,
    WorkflowMetadataUpdate,
    WorkflowPublic,
    WorkflowUpdate,
    WorkflowVisualsCreate,
    WorkflowVisualsPublic,
    WorkflowVisualsUpdate,
)

__all__ = [
    # Enums
    'NodeConfigType',
    'ExecutionMode',
    'EdgeType',
    'StepExecutionType',
    'ReducerType',
    'PathType',
    'ExecutionStatus',
    'NodeExecutionStatus',
    # Workflow schemas
    'WorkflowCreate',
    'WorkflowUpdate',
    'WorkflowPublic',
    # Node schemas
    'LogicalNodeCreate',
    'LogicalNodeUpdate',
    'LogicalNodePublic',
    'LogicalNodeInputCreate',
    'LogicalNodeInputPublic',
    'LogicalNodeOutputCreate',
    'LogicalNodeOutputPublic',
    # Edge schemas
    'LogicalEdgeCreate',
    'LogicalEdgeUpdate',
    'LogicalEdgePublic',
    # Visuals schemas
    'WorkflowVisualsCreate',
    'WorkflowVisualsUpdate',
    'WorkflowVisualsPublic',
    'NodeVisualsCreate',
    'NodeVisualsUpdate',
    'NodeVisualsPublic',
    'EdgeVisualsCreate',
    'EdgeVisualsUpdate',
    'EdgeVisualsPublic',
    # Metadata schemas
    'WorkflowMetadataCreate',
    'WorkflowMetadataUpdate',
    'WorkflowMetadataPublic',
    # Execution schemas
    'WorkflowExecutionCreate',
    'WorkflowExecutionUpdate',
    'WorkflowExecutionPublic',
]
