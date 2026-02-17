"""Workflow, node, and edge visuals Pydantic schemas (Create, Update, Public)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from workflow_models.enums import PathType

# ============================================
# WORKFLOW VISUALS
# ============================================


class WorkflowVisualsCreate(BaseModel):
    """Schema for creating workflow visuals."""

    id: UUID
    canvas_version: str = Field(default='2.0', max_length=10)
    viewport: dict[str, Any] = Field(default_factory=lambda: {'zoom': 1.0, 'x': 0, 'y': 0})


class WorkflowVisualsUpdate(BaseModel):
    """Schema for updating workflow visuals."""

    canvas_version: str | None = Field(default=None, max_length=10)
    viewport: dict[str, Any] | None = None


class WorkflowVisualsPublic(BaseModel):
    """Schema for public workflow visuals representation."""

    id: UUID
    canvas_version: str = Field(default='2.0', max_length=10)
    viewport: dict[str, Any] = Field(default_factory=lambda: {'zoom': 1.0, 'x': 0, 'y': 0})
    updated_at: datetime


# ============================================
# NODE VISUALS
# ============================================


class NodeVisualsCreate(BaseModel):
    """Schema for creating node visuals."""

    id: UUID
    workflow_id: UUID
    position_x: float
    position_y: float
    width: int = Field(default=180)
    height: int = Field(default=80)
    style: dict[str, Any] = Field(default_factory=dict)
    collapsed: bool = Field(default=False)


class NodeVisualsUpdate(BaseModel):
    """Schema for updating node visuals."""

    position_x: float | None = None
    position_y: float | None = None
    width: int | None = None
    height: int | None = None
    style: dict[str, Any] | None = None
    collapsed: bool | None = None


class NodeVisualsPublic(BaseModel):
    """Schema for public node visuals representation."""

    id: UUID
    workflow_id: UUID
    position_x: float
    position_y: float
    width: int = Field(default=180)
    height: int = Field(default=80)
    style: dict[str, Any] = Field(default_factory=dict)
    collapsed: bool = Field(default=False)
    updated_at: datetime


# ============================================
# EDGE VISUALS
# ============================================


class EdgeVisualsCreate(BaseModel):
    """Schema for creating edge visuals."""

    id: UUID  # The ID is the edge's ID
    workflow_id: UUID
    path_type: PathType = Field(default=PathType.BEZIER)
    style: dict[str, Any] = Field(default_factory=dict)
    animated: bool = Field(default=False)


class EdgeVisualsUpdate(BaseModel):
    """Schema for updating edge visuals."""

    path_type: PathType | None = None
    style: dict[str, Any] | None = None
    animated: bool | None = None


class EdgeVisualsPublic(BaseModel):
    """Schema for public edge visuals representation."""

    id: UUID  # The ID is the edge's ID
    workflow_id: UUID
    path_type: PathType = Field(default=PathType.BEZIER)
    style: dict[str, Any] = Field(default_factory=dict)
    animated: bool = Field(default=False)
    updated_at: datetime
