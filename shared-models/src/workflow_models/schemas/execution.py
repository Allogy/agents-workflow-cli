"""Workflow execution Pydantic schemas (Create, Update, Public)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from workflow_models.enums import ExecutionStatus


class WorkflowExecutionCreate(BaseModel):
    """Schema for creating a workflow execution record."""

    workflow_id: UUID
    workflow_version: int
    status: ExecutionStatus


class WorkflowExecutionUpdate(BaseModel):
    """Schema for updating a workflow execution record."""

    status: ExecutionStatus | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    execution_context: dict[str, Any] | None = None


class WorkflowExecutionPublic(BaseModel):
    """Schema for public workflow execution representation."""

    execution_id: UUID
    workflow_id: UUID
    workflow_version: int
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None
    execution_context: dict[str, Any]
