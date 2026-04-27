"""
Shared workflow models for the Agents Platform.

This package provides Pydantic v2 schemas and enums for workflow data contracts,
shared between the backend API and the workflow CLI. It has zero SQLAlchemy or
SQLModel dependencies.
"""

__version__ = '0.1.2'

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

# WDF (Workflow Definition Format) models — YAML-based workflow definitions
# Import via `from workflow_models.wdf import ...` for the full API.
# Top-level re-exports provided here for convenience.
from workflow_models.wdf import (
    EdgeDefinition,
    NodeDefinition,
    WorkflowDefinition,
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
    # Workflow schemas (API-oriented)
    'WorkflowCreate',
    'WorkflowUpdate',
    'WorkflowPublic',
    # Node schemas (API-oriented)
    'LogicalNodeCreate',
    'LogicalNodeUpdate',
    'LogicalNodePublic',
    'LogicalNodeInputCreate',
    'LogicalNodeInputPublic',
    'LogicalNodeOutputCreate',
    'LogicalNodeOutputPublic',
    # Edge schemas (API-oriented)
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
    # WDF models (YAML-oriented)
    'WorkflowDefinition',
    'NodeDefinition',
    'EdgeDefinition',
]
