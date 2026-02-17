"""Tests for workflow Pydantic schemas.

Verifies that all workflow-related Pydantic schemas can be instantiated,
validated, and serialized, matching the acceptance criteria from RAG-941:
  - All workflow-related Pydantic schemas (Create, Update, Public variants)
  - Zero SQLAlchemy or SQLModel dependencies
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from workflow_models.enums import (
    EdgeType,
    ExecutionMode,
    ExecutionStatus,
    NodeConfigType,
    PathType,
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

# ============================================
# WORKFLOW SCHEMAS
# ============================================


class TestWorkflowCreate:
    def test_valid_create(self):
        wf = WorkflowCreate(
            version=1,
            state_schema={'input': 'string'},
            organization_id=uuid4(),
        )
        assert wf.version == 1
        assert wf.entry_point is None
        assert wf.exit_point is None
        assert wf.execution_config == {}

    def test_with_all_fields(self):
        entry = uuid4()
        exit_ = uuid4()
        wf = WorkflowCreate(
            version=2,
            entry_point=entry,
            exit_point=exit_,
            state_schema={'key': 'value'},
            execution_config={'timeout': 300},
            organization_id=uuid4(),
        )
        assert wf.entry_point == entry
        assert wf.exit_point == exit_
        assert wf.execution_config == {'timeout': 300}

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            WorkflowCreate()  # type: ignore[call-arg]


class TestWorkflowUpdate:
    def test_all_none_by_default(self):
        wu = WorkflowUpdate()
        assert wu.version is None
        assert wu.entry_point is None
        assert wu.state_schema is None

    def test_partial_update(self):
        wu = WorkflowUpdate(version=3)
        assert wu.version == 3
        assert wu.state_schema is None


class TestWorkflowPublic:
    def test_valid_public(self):
        now = datetime.now(UTC)
        wp = WorkflowPublic(
            id=uuid4(),
            version=1,
            state_schema={},
            organization_id=uuid4(),
            created_by=uuid4(),
            created_at=now,
            updated_at=now,
        )
        assert wp.version == 1

    def test_serialization_roundtrip(self):
        now = datetime.now(UTC)
        wp = WorkflowPublic(
            id=uuid4(),
            version=1,
            state_schema={'input': 'string'},
            execution_config={'timeout': 60},
            organization_id=uuid4(),
            created_by=uuid4(),
            created_at=now,
            updated_at=now,
        )
        data = wp.model_dump()
        wp2 = WorkflowPublic.model_validate(data)
        assert wp == wp2


# ============================================
# NODE SCHEMAS
# ============================================


class TestLogicalNodeCreate:
    def test_valid_create(self):
        node = LogicalNodeCreate(
            workflow_id=uuid4(),
            workflow_version=1,
            config_type=NodeConfigType.AGENT,
            execution_mode=ExecutionMode.INPUT,
        )
        assert node.config_type == NodeConfigType.AGENT
        assert node.delegated_response is False
        assert node.step_type == StepExecutionType.STEP
        assert node.timeout_seconds == 30
        assert node.retry_policy == {'max_retries': 3}

    def test_with_optional_id(self):
        node_id = uuid4()
        node = LogicalNodeCreate(
            id=node_id,
            workflow_id=uuid4(),
            workflow_version=1,
            config_type=NodeConfigType.LLM_CALL,
            execution_mode=ExecutionMode.OUTPUT,
        )
        assert node.id == node_id

    def test_invalid_config_type(self):
        with pytest.raises(ValidationError):
            LogicalNodeCreate(
                workflow_id=uuid4(),
                workflow_version=1,
                config_type='INVALID_TYPE',  # type: ignore[arg-type]
                execution_mode=ExecutionMode.INPUT,
            )


class TestLogicalNodeUpdate:
    def test_all_none_by_default(self):
        update = LogicalNodeUpdate()
        assert update.config_type is None
        assert update.execution_mode is None
        assert update.timeout_seconds is None

    def test_partial_update(self):
        update = LogicalNodeUpdate(
            config_type=NodeConfigType.RAG_AGENT,
            timeout_seconds=60,
        )
        assert update.config_type == NodeConfigType.RAG_AGENT
        assert update.timeout_seconds == 60


class TestLogicalNodePublic:
    def test_valid_public(self):
        node = LogicalNodePublic(
            id=uuid4(),
            workflow_id=uuid4(),
            workflow_version=1,
            config_type=NodeConfigType.HUMAN_REVIEW,
            execution_mode=ExecutionMode.FLOW,
            function_name=None,
            parameters={},
            retry_policy={'max_retries': 3},
            timeout_seconds=30,
            config={},
            delegated_response=False,
            step_type=StepExecutionType.STEP,
            join_config={},
            created_at=datetime.now(UTC),
        )
        assert node.config_type == NodeConfigType.HUMAN_REVIEW


class TestLogicalNodeInputCreate:
    def test_valid_create(self):
        inp = LogicalNodeInputCreate(
            node_id=uuid4(),
            input_name='user_query',
            sequence_order=0,
        )
        assert inp.input_name == 'user_query'
        assert inp.sequence_order == 0


class TestLogicalNodeInputPublic:
    def test_valid_public(self):
        inp = LogicalNodeInputPublic(
            id=uuid4(),
            node_id=uuid4(),
            workflow_id=uuid4(),
            input_name='context',
            sequence_order=1,
            created_at=datetime.now(UTC),
        )
        assert inp.input_name == 'context'


class TestLogicalNodeOutputCreate:
    def test_valid_create(self):
        out = LogicalNodeOutputCreate(
            node_id=uuid4(),
            output_name='response',
            sequence_order=0,
        )
        assert out.output_name == 'response'


class TestLogicalNodeOutputPublic:
    def test_valid_public(self):
        out = LogicalNodeOutputPublic(
            id=uuid4(),
            node_id=uuid4(),
            workflow_id=uuid4(),
            output_name='result',
            sequence_order=0,
        )
        assert out.output_name == 'result'


# ============================================
# EDGE SCHEMAS
# ============================================


class TestLogicalEdgeCreate:
    def test_valid_create(self):
        edge = LogicalEdgeCreate(
            workflow_id=uuid4(),
            workflow_version=1,
            source_node_id=uuid4(),
            target_node_id=uuid4(),
        )
        assert edge.edge_type == EdgeType.STATIC
        assert edge.data_mapping == {}

    def test_conditional_edge(self):
        edge = LogicalEdgeCreate(
            workflow_id=uuid4(),
            workflow_version=1,
            edge_type=EdgeType.CONDITIONAL,
            condition_function='check_approval',
            source_node_id=uuid4(),
            target_node_id=uuid4(),
        )
        assert edge.edge_type == EdgeType.CONDITIONAL
        assert edge.condition_function == 'check_approval'


class TestLogicalEdgeUpdate:
    def test_all_none_by_default(self):
        update = LogicalEdgeUpdate()
        assert update.edge_type is None
        assert update.data_mapping is None


class TestLogicalEdgePublic:
    def test_valid_public(self):
        edge = LogicalEdgePublic(
            id=uuid4(),
            workflow_id=uuid4(),
            workflow_version=1,
            source_node_id=uuid4(),
            target_node_id=uuid4(),
            created_at=datetime.now(UTC),
        )
        assert edge.edge_type == EdgeType.STATIC


# ============================================
# VISUALS SCHEMAS
# ============================================


class TestWorkflowVisualsCreate:
    def test_valid_create_with_defaults(self):
        vis = WorkflowVisualsCreate(id=uuid4())
        assert vis.canvas_version == '2.0'
        assert vis.viewport == {'zoom': 1.0, 'x': 0, 'y': 0}


class TestWorkflowVisualsUpdate:
    def test_all_none_by_default(self):
        update = WorkflowVisualsUpdate()
        assert update.canvas_version is None
        assert update.viewport is None


class TestWorkflowVisualsPublic:
    def test_valid_public(self):
        vis = WorkflowVisualsPublic(
            id=uuid4(),
            updated_at=datetime.now(UTC),
        )
        assert vis.canvas_version == '2.0'


class TestNodeVisualsCreate:
    def test_valid_create(self):
        vis = NodeVisualsCreate(
            id=uuid4(),
            workflow_id=uuid4(),
            position_x=100.0,
            position_y=200.0,
        )
        assert vis.width == 180
        assert vis.height == 80
        assert vis.collapsed is False


class TestNodeVisualsUpdate:
    def test_all_none_by_default(self):
        update = NodeVisualsUpdate()
        assert update.position_x is None
        assert update.collapsed is None


class TestNodeVisualsPublic:
    def test_valid_public(self):
        vis = NodeVisualsPublic(
            id=uuid4(),
            workflow_id=uuid4(),
            position_x=50.0,
            position_y=75.0,
            updated_at=datetime.now(UTC),
        )
        assert vis.width == 180


class TestEdgeVisualsCreate:
    def test_valid_create(self):
        vis = EdgeVisualsCreate(
            id=uuid4(),
            workflow_id=uuid4(),
        )
        assert vis.path_type == PathType.BEZIER
        assert vis.animated is False


class TestEdgeVisualsUpdate:
    def test_all_none_by_default(self):
        update = EdgeVisualsUpdate()
        assert update.path_type is None
        assert update.animated is None


class TestEdgeVisualsPublic:
    def test_valid_public(self):
        vis = EdgeVisualsPublic(
            id=uuid4(),
            workflow_id=uuid4(),
            updated_at=datetime.now(UTC),
        )
        assert vis.path_type == PathType.BEZIER


# ============================================
# METADATA SCHEMAS
# ============================================


class TestWorkflowMetadataCreate:
    def test_valid_create_minimal(self):
        meta = WorkflowMetadataCreate(workflow_id=uuid4())
        assert meta.name is None
        assert meta.tags == []
        assert meta.is_active is True

    def test_valid_create_full(self):
        meta = WorkflowMetadataCreate(
            workflow_id=uuid4(),
            owner_id=uuid4(),
            name='Document Processing',
            description='A workflow for processing documents',
            tags=['production', 'documents'],
            is_active=True,
            custom_fields={'category': 'automation'},
        )
        assert meta.name == 'Document Processing'
        assert len(meta.tags) == 2


class TestWorkflowMetadataUpdate:
    def test_all_none_by_default(self):
        update = WorkflowMetadataUpdate()
        assert update.name is None
        assert update.tags is None
        assert update.is_active is None


class TestWorkflowMetadataPublic:
    def test_valid_public(self):
        now = datetime.now(UTC)
        meta = WorkflowMetadataPublic(
            workflow_id=uuid4(),
            owner_id=uuid4(),
            name='Test Workflow',
            created_at=now,
            updated_at=now,
        )
        assert meta.name == 'Test Workflow'


# ============================================
# EXECUTION SCHEMAS
# ============================================


class TestWorkflowExecutionCreate:
    def test_valid_create(self):
        exe = WorkflowExecutionCreate(
            workflow_id=uuid4(),
            workflow_version=1,
            status=ExecutionStatus.PENDING,
        )
        assert exe.status == ExecutionStatus.PENDING


class TestWorkflowExecutionUpdate:
    def test_all_none_by_default(self):
        update = WorkflowExecutionUpdate()
        assert update.status is None
        assert update.completed_at is None

    def test_mark_completed(self):
        update = WorkflowExecutionUpdate(
            status=ExecutionStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        assert update.status == ExecutionStatus.COMPLETED


class TestWorkflowExecutionPublic:
    def test_valid_public(self):
        exe = WorkflowExecutionPublic(
            execution_id=uuid4(),
            workflow_id=uuid4(),
            workflow_version=1,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
            execution_context={},
        )
        assert exe.status == ExecutionStatus.RUNNING
