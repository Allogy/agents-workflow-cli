"""Logical edge Pydantic schemas (Create, Update, Public)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from workflow_models.enums import EdgeType


class LogicalEdgeCreate(BaseModel):
    """Schema for creating a new logical edge."""

    workflow_id: UUID
    workflow_version: int
    edge_type: EdgeType = Field(default=EdgeType.STATIC)
    condition_function: str | None = Field(default=None, max_length=255)
    data_mapping: dict[str, Any] = Field(default_factory=dict)
    source_node_id: UUID
    target_node_id: UUID


class LogicalEdgeUpdate(BaseModel):
    """Schema for updating an existing logical edge."""

    workflow_version: int | None = None
    edge_type: EdgeType | None = None
    condition_function: str | None = Field(default=None, max_length=255)
    data_mapping: dict[str, Any] | None = None


class LogicalEdgePublic(BaseModel):
    """Schema for public logical edge representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    workflow_version: int
    edge_type: EdgeType = Field(default=EdgeType.STATIC)
    condition_function: str | None = Field(default=None, max_length=255)
    data_mapping: dict[str, Any] = Field(default_factory=dict)
    source_node_id: UUID
    target_node_id: UUID
    created_at: datetime
