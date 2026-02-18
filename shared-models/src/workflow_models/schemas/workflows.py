"""Workflow core Pydantic schemas (Create, Update, Public)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    """Schema for creating a new workflow."""

    version: int
    entry_point: UUID | None = None
    exit_point: UUID | None = None
    state_schema: dict[str, Any] = Field(default_factory=dict)
    execution_config: dict[str, Any] = Field(default_factory=dict)
    organization_id: UUID


class WorkflowUpdate(BaseModel):
    """Schema for updating an existing workflow."""

    version: int | None = None
    entry_point: UUID | None = None
    exit_point: UUID | None = None
    state_schema: dict[str, Any] | None = None
    execution_config: dict[str, Any] | None = None


class WorkflowPublic(BaseModel):
    """Schema for public workflow representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    entry_point: UUID | None = None
    exit_point: UUID | None = None
    state_schema: dict[str, Any] = Field(default_factory=dict)
    execution_config: dict[str, Any] = Field(default_factory=dict)
    organization_id: UUID
    created_by: UUID
    created_at: datetime
    updated_at: datetime
