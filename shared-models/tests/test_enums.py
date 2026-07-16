"""Tests for workflow enums.

Verifies that all workflow-related enums are present with the correct values,
matching the acceptance criteria from RAG-941:
  - All workflow enums (NodeConfigType, EdgeType, ExecutionMode, etc.)
  - Zero SQLAlchemy or SQLModel dependencies
"""

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


class TestNodeConfigType:
    """NodeConfigType should expose the expected node type names."""

    def test_has_expected_member_names(self):
        assert {member.name for member in NodeConfigType} == {
            'AGENT',
            'API_CONSUMPTION',
            'CHAT_INPUT',
            'DOCUMENT_EXTRACTION',
            'FILE_UPLOAD',
            'HUMAN_REVIEW',
            'ITERATOR',
            'LLM_CALL',
            'MEMORY_FILE_URL',
            'PLAIN_TXT_INPUT',
            'RAG_AGENT',
            'RETRIEVE',
            'SELECTION',
            'STRUCTURED_INPUT',
            'STRUCTURED_OUTPUT',
        }

    def test_integration_nodes(self):
        assert NodeConfigType.API_CONSUMPTION == 'API_CONSUMPTION'

    def test_agent_nodes(self):
        assert NodeConfigType.AGENT == 'AGENT'
        assert NodeConfigType.RAG_AGENT == 'RAG_AGENT'

    def test_llm_nodes(self):
        assert NodeConfigType.LLM_CALL == 'LLM_CALL'
        assert NodeConfigType.STRUCTURED_OUTPUT == 'STRUCTURED_OUTPUT'

    def test_data_nodes(self):
        assert NodeConfigType.RETRIEVE == 'RETRIEVE'

    def test_input_nodes(self):
        assert NodeConfigType.PLAIN_TXT_INPUT == 'PLAIN_TXT_INPUT'
        assert NodeConfigType.STRUCTURED_INPUT == 'STRUCTURED_INPUT'
        assert NodeConfigType.FILE_UPLOAD == 'FILE_UPLOAD'
        assert NodeConfigType.CHAT_INPUT == 'CHAT_INPUT'

    def test_output_sharing_nodes(self):
        assert NodeConfigType.MEMORY_FILE_URL == 'MEMORY_FILE_URL'

    def test_human_interaction_nodes(self):
        assert NodeConfigType.HUMAN_REVIEW == 'HUMAN_REVIEW'

    def test_other_nodes(self):
        assert NodeConfigType.SELECTION == 'SELECTION'
        assert NodeConfigType.ITERATOR == 'ITERATOR'
        assert NodeConfigType.DOCUMENT_EXTRACTION == 'DOCUMENT_EXTRACTION'

    def test_is_str_enum(self):
        assert isinstance(NodeConfigType.AGENT, str)


class TestExecutionMode:
    """ExecutionMode should have INPUT, OUTPUT, MESSAGES, FLOW."""

    def test_has_4_values(self):
        assert len(ExecutionMode) == 4

    def test_values(self):
        assert ExecutionMode.INPUT == 'INPUT'
        assert ExecutionMode.OUTPUT == 'OUTPUT'
        assert ExecutionMode.MESSAGES == 'MESSAGES'
        assert ExecutionMode.FLOW == 'FLOW'


class TestEdgeType:
    """EdgeType should have STATIC, CONDITIONAL, METADATA, RECURSIVE, MAPPING."""

    def test_has_5_values(self):
        assert len(EdgeType) == 5

    def test_values(self):
        assert EdgeType.STATIC == 'STATIC'
        assert EdgeType.CONDITIONAL == 'CONDITIONAL'
        assert EdgeType.METADATA == 'METADATA'
        assert EdgeType.RECURSIVE == 'RECURSIVE'
        assert EdgeType.MAPPING == 'MAPPING'


class TestStepExecutionType:
    """StepExecutionType should have STEP, STREAM, JOIN, INPUT."""

    def test_has_4_values(self):
        assert len(StepExecutionType) == 4

    def test_values(self):
        assert StepExecutionType.STEP == 'STEP'
        assert StepExecutionType.STREAM == 'STREAM'
        assert StepExecutionType.JOIN == 'JOIN'
        assert StepExecutionType.INPUT == 'INPUT'


class TestReducerType:
    """ReducerType should have 7 reducer types."""

    def test_has_7_values(self):
        assert len(ReducerType) == 7

    def test_values(self):
        assert ReducerType.REDUCE_NULL == 'REDUCE_NULL'
        assert ReducerType.REDUCE_LIST_APPEND == 'REDUCE_LIST_APPEND'
        assert ReducerType.REDUCE_DICT_MERGE == 'REDUCE_DICT_MERGE'
        assert ReducerType.REDUCE_SUM == 'REDUCE_SUM'
        assert ReducerType.REDUCE_FIRST == 'REDUCE_FIRST'
        assert ReducerType.REDUCE_LAST == 'REDUCE_LAST'
        assert ReducerType.CUSTOM == 'CUSTOM'


class TestPathType:
    def test_has_3_values(self):
        assert len(PathType) == 3

    def test_values(self):
        assert PathType.BEZIER == 'BEZIER'
        assert PathType.STRAIGHT == 'STRAIGHT'
        assert PathType.STEP == 'STEP'


class TestExecutionStatus:
    def test_has_9_values(self):
        assert len(ExecutionStatus) == 9

    def test_values(self):
        assert ExecutionStatus.PENDING == 'PENDING'
        assert ExecutionStatus.RUNNING == 'RUNNING'
        assert ExecutionStatus.PAUSED == 'PAUSED'
        assert ExecutionStatus.COMPLETED == 'COMPLETED'
        assert ExecutionStatus.FAILED == 'FAILED'
        assert ExecutionStatus.CANCELLED == 'CANCELLED'
        assert ExecutionStatus.TIMED_OUT == 'TIMED_OUT'
        assert ExecutionStatus.WAITING_FOR_REVIEW == 'WAITING_FOR_REVIEW'
        assert ExecutionStatus.WAITING_FOR_INPUT == 'WAITING_FOR_INPUT'


class TestNodeExecutionStatus:
    def test_has_6_values(self):
        assert len(NodeExecutionStatus) == 6

    def test_values(self):
        assert NodeExecutionStatus.PENDING == 'PENDING'
        assert NodeExecutionStatus.RUNNING == 'RUNNING'
        assert NodeExecutionStatus.WAITING_INPUT == 'WAITING_INPUT'
        assert NodeExecutionStatus.COMPLETED == 'COMPLETED'
        assert NodeExecutionStatus.FAILED == 'FAILED'
        assert NodeExecutionStatus.SKIPPED == 'SKIPPED'
