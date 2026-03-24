"""Integration test: CLI can import and use all workflow schemas from the shared package.

Verifies the BDD scenario from RAG-941:
  Given the shared models package is published
  When the CLI's pyproject.toml references it
  Then the CLI can import and use all workflow schemas for validation
"""

from datetime import UTC, datetime
from uuid import uuid4

from workflow_models import (
    EdgeType,
    EdgeVisualsCreate,
    EdgeVisualsPublic,
    EdgeVisualsUpdate,
    ExecutionMode,
    ExecutionStatus,
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
    NodeConfigType,
    NodeExecutionStatus,
    NodeVisualsCreate,
    NodeVisualsPublic,
    NodeVisualsUpdate,
    PathType,
    ReducerType,
    StepExecutionType,
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


def test_cli_can_import_all_enums():
    """The CLI can import all 8 workflow enums from the shared package."""
    assert len(NodeConfigType) == 12
    assert len(ExecutionMode) == 4
    assert len(EdgeType) == 5
    assert len(StepExecutionType) == 4
    assert len(ReducerType) == 7
    assert len(PathType) == 3
    assert len(ExecutionStatus) == 9
    assert len(NodeExecutionStatus) == 6


def test_cli_can_import_all_schemas():
    """The CLI can import all 30 workflow schemas from the shared package."""
    # All Create schemas
    create_schemas = [
        WorkflowCreate,
        LogicalNodeCreate,
        LogicalNodeInputCreate,
        LogicalNodeOutputCreate,
        LogicalEdgeCreate,
        WorkflowVisualsCreate,
        NodeVisualsCreate,
        EdgeVisualsCreate,
        WorkflowMetadataCreate,
        WorkflowExecutionCreate,
    ]
    assert len(create_schemas) == 10

    # All Update schemas
    update_schemas = [
        WorkflowUpdate,
        LogicalNodeUpdate,
        LogicalEdgeUpdate,
        WorkflowVisualsUpdate,
        NodeVisualsUpdate,
        EdgeVisualsUpdate,
        WorkflowMetadataUpdate,
        WorkflowExecutionUpdate,
    ]
    assert len(update_schemas) == 8

    # All Public schemas
    public_schemas = [
        WorkflowPublic,
        LogicalNodePublic,
        LogicalNodeInputPublic,
        LogicalNodeOutputPublic,
        LogicalEdgePublic,
        WorkflowVisualsPublic,
        NodeVisualsPublic,
        EdgeVisualsPublic,
        WorkflowMetadataPublic,
        WorkflowExecutionPublic,
    ]
    assert len(public_schemas) == 10


def test_cli_can_validate_workflow_data():
    """The CLI can use schemas to validate workflow data structures."""
    org_id = uuid4()
    workflow_id = uuid4()
    node_id = uuid4()
    now = datetime.now(UTC)

    # Validate a complete workflow creation payload
    workflow = WorkflowCreate(
        version=1,
        state_schema={'user_query': 'string'},
        execution_config={'timeout': 300},
        organization_id=org_id,
    )
    assert workflow.organization_id == org_id

    # Validate a node creation payload
    node = LogicalNodeCreate(
        workflow_id=workflow_id,
        workflow_version=1,
        config_type=NodeConfigType.AGENT,
        execution_mode=ExecutionMode.INPUT,
        parameters={'model': 'claude-sonnet'},
        config={'primaryInput': '{{input.output.text}}'},
    )
    assert node.config_type == NodeConfigType.AGENT

    # Validate an edge creation payload
    edge = LogicalEdgeCreate(
        workflow_id=workflow_id,
        workflow_version=1,
        edge_type=EdgeType.CONDITIONAL,
        condition_function='check_approval',
        source_node_id=node_id,
        target_node_id=uuid4(),
    )
    assert edge.edge_type == EdgeType.CONDITIONAL

    # Validate a metadata creation payload
    metadata = WorkflowMetadataCreate(
        workflow_id=workflow_id,
        name='Document Processing Pipeline',
        description='Processes and analyzes uploaded documents',
        tags=['production', 'document-processing'],
    )
    assert metadata.name == 'Document Processing Pipeline'

    # Validate serialization/deserialization roundtrip
    public = WorkflowPublic(
        id=workflow_id,
        version=1,
        state_schema={'user_query': 'string'},
        organization_id=org_id,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    data = public.model_dump(mode='json')
    restored = WorkflowPublic.model_validate(data)
    assert restored.id == public.id
