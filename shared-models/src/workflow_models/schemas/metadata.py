"""Workflow metadata Pydantic schemas (Create, Update, Public)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowMetadataCreate(BaseModel):
    """Schema for creating workflow metadata."""

    workflow_id: UUID
    owner_id: UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_active: bool = Field(default=True)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class WorkflowMetadataUpdate(BaseModel):
    """Schema for updating workflow metadata."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    tags: list[str] | None = None
    owner_id: UUID | None = None
    is_active: bool | None = None
    custom_fields: dict[str, Any] | None = None


class WorkflowMetadataPublic(BaseModel):
    """Schema for public workflow metadata representation."""

    model_config = ConfigDict(from_attributes=True)

    workflow_id: UUID
    owner_id: UUID | None
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_active: bool = Field(default=True)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
