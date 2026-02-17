"""Logical node Pydantic schemas (Create, Update, Public) and input/output schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from workflow_models.enums import ExecutionMode, NodeConfigType, StepExecutionType


class LogicalNodeCreate(BaseModel):
    """Schema for creating a new logical node."""

    id: UUID | None = None  # Optional: client can specify ID or let it be auto-generated
    workflow_id: UUID
    workflow_version: int
    config_type: NodeConfigType
    execution_mode: ExecutionMode
    function_name: str | None = Field(default=None, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=lambda: {'max_retries': 3})
    timeout_seconds: int = Field(default=30)
    config: dict[str, Any] = Field(default_factory=dict)
    delegated_response: bool = Field(default=False)
    step_type: StepExecutionType = Field(default=StepExecutionType.STEP)
    join_config: dict[str, Any] = Field(
        default_factory=dict,
        description='Configuration for JOIN step types. Includes reducer_type, initial_value, and custom_reducer.',
    )


class LogicalNodeUpdate(BaseModel):
    """Schema for updating an existing logical node."""

    workflow_version: int | None = None
    config_type: NodeConfigType | None = None
    execution_mode: ExecutionMode | None = None
    function_name: str | None = Field(default=None, max_length=255)
    parameters: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    config: dict[str, Any] | None = None
    delegated_response: bool | None = None
    step_type: StepExecutionType | None = None
    join_config: dict[str, Any] | None = None


class LogicalNodePublic(BaseModel):
    """Schema for public logical node representation."""

    id: UUID
    workflow_id: UUID
    workflow_version: int
    config_type: NodeConfigType
    execution_mode: ExecutionMode
    function_name: str | None
    parameters: dict[str, Any]
    retry_policy: dict[str, Any]
    timeout_seconds: int
    config: dict[str, Any]
    delegated_response: bool
    step_type: StepExecutionType
    join_config: dict[str, Any]
    created_at: datetime


# ============================================
# NODE INPUT/OUTPUT SCHEMAS
# ============================================


class LogicalNodeInputCreate(BaseModel):
    """Schema for creating a logical node input."""

    node_id: UUID
    input_name: str = Field(max_length=255)
    sequence_order: int


class LogicalNodeInputPublic(BaseModel):
    """Schema for public logical node input representation."""

    id: UUID
    node_id: UUID
    workflow_id: UUID
    input_name: str = Field(max_length=255)
    sequence_order: int
    created_at: datetime


class LogicalNodeOutputCreate(BaseModel):
    """Schema for creating a logical node output."""

    node_id: UUID
    output_name: str = Field(max_length=255)
    sequence_order: int


class LogicalNodeOutputPublic(BaseModel):
    """Schema for public logical node output representation."""

    id: UUID
    node_id: UUID
    workflow_id: UUID
    output_name: str = Field(max_length=255)
    sequence_order: int
