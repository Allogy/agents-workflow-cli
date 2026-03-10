"""Tests for WDF WorkflowDefinition and EdgeDefinition models.

Verifies the top-level workflow definition and edge models work correctly
for parsing .workflow.yaml files.

BDD Scenarios from RAG-945:
  Scenario: All 10 node types have config schemas
  Scenario: YAML round-trip preserves data (model-level tests here)
"""

import pytest
from pydantic import ValidationError

from workflow_models.wdf.edges import EdgeDefinition
from workflow_models.wdf.workflow import WorkflowDefinition

# ============================================
# EDGE DEFINITION
# ============================================


class TestEdgeDefinition:
    """EdgeDefinition: from (slug), to (slug), type (defaults STATIC), condition."""

    def test_static_edge_minimal(self):
        """Static edges only need from and to — type defaults to STATIC."""
        edge = EdgeDefinition(**{'from': 'upload', 'to': 'extract'})
        assert edge.from_node == 'upload'
        assert edge.to == 'extract'
        assert edge.type == 'STATIC'

    def test_conditional_edge(self):
        edge = EdgeDefinition(
            **{
                'from': 'classify',
                'to': 'approve',
                'type': 'CONDITIONAL',
                'condition': "output.category == 'SUPPLIES'",
            }
        )
        assert edge.from_node == 'classify'
        assert edge.to == 'approve'
        assert edge.type == 'CONDITIONAL'
        assert edge.condition == "output.category == 'SUPPLIES'"

    def test_edge_with_explicit_type(self):
        edge = EdgeDefinition(
            **{
                'from': 'iterator',
                'to': 'process_item',
                'type': 'MAPPING',
            }
        )
        assert edge.type == 'MAPPING'

    def test_all_valid_edge_types(self):
        """All 5 edge types from the EdgeType enum should be accepted."""
        for edge_type in ('STATIC', 'CONDITIONAL', 'METADATA', 'RECURSIVE', 'MAPPING'):
            edge = EdgeDefinition(**{'from': 'a', 'to': 'b', 'type': edge_type})
            assert edge.type == edge_type

    def test_invalid_edge_type_raises(self):
        """Invalid edge type strings should be rejected."""
        with pytest.raises(ValidationError):
            EdgeDefinition(**{'from': 'a', 'to': 'b', 'type': 'INVALID'})

    def test_missing_from_raises(self):
        with pytest.raises(ValidationError):
            EdgeDefinition(**{'to': 'extract'})  # type: ignore[arg-type]

    def test_missing_to_raises(self):
        with pytest.raises(ValidationError):
            EdgeDefinition(**{'from': 'upload'})  # type: ignore[arg-type]

    def test_serialization_uses_from_key(self):
        """When serialized to dict (for YAML output), should use 'from' not 'from_node'."""
        edge = EdgeDefinition(**{'from': 'a', 'to': 'b'})
        data = edge.model_dump(by_alias=True, exclude_none=True)
        assert 'from' in data
        assert 'from_node' not in data
        assert data['from'] == 'a'
        assert data['to'] == 'b'


# ============================================
# WORKFLOW DEFINITION
# ============================================


class TestWorkflowDefinition:
    """WorkflowDefinition: top-level model for .workflow.yaml files."""

    def test_valid_minimal_workflow(self):
        wf = WorkflowDefinition(
            name='Simple Workflow',
            nodes={
                'input': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'label': 'Enter Question',
                    'config': {},
                },
                'agent': {
                    'type': 'agent',
                    'execution_mode': 'MESSAGES',
                    'label': 'Process',
                    'config': {},
                },
            },
            edges=[
                {'from': 'input', 'to': 'agent'},
            ],
            entry='input',
            exit='agent',
        )
        assert wf.name == 'Simple Workflow'
        assert len(wf.nodes) == 2
        assert len(wf.edges) == 1
        assert wf.entry == 'input'
        assert wf.exit == 'agent'

    def test_workflow_with_all_optional_fields(self):
        wf = WorkflowDefinition(
            name='Full Workflow',
            description='A complete workflow with all fields',
            version=2,
            tags=['finance', 'document-processing'],
            state_schema={'inputs': {}, 'outputs': {}, 'variables': {}},
            nodes={
                'start': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'config': {},
                },
            },
            edges=[],
            entry='start',
            exit='start',
        )
        assert wf.description == 'A complete workflow with all fields'
        assert wf.version == 2
        assert wf.tags == ['finance', 'document-processing']
        assert wf.state_schema == {'inputs': {}, 'outputs': {}, 'variables': {}}

    def test_version_defaults_to_1(self):
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        assert wf.version == 1

    def test_state_schema_defaults_to_none(self):
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        assert wf.state_schema is None

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[],
                entry='n',
                exit='n',
            )  # type: ignore[call-arg]

    def test_missing_nodes_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name='Test',
                edges=[],
                entry='n',
                exit='n',
            )  # type: ignore[call-arg]

    def test_missing_entry_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[],
                exit='n',
            )  # type: ignore[call-arg]

    def test_missing_exit_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[],
                entry='n',
            )  # type: ignore[call-arg]

    def test_entry_must_reference_existing_node(self):
        """Entry point must be a key in the nodes dict."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[],
                entry='nonexistent',
                exit='n',
            )
        assert 'entry' in str(exc_info.value).lower()

    def test_exit_must_reference_existing_node(self):
        """Exit point must be a key in the nodes dict."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[],
                entry='n',
                exit='nonexistent',
            )
        assert 'exit' in str(exc_info.value).lower()

    def test_edge_from_must_reference_existing_node(self):
        """Edges must reference valid node slugs."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[{'from': 'nonexistent', 'to': 'n'}],
                entry='n',
                exit='n',
            )
        assert 'nonexistent' in str(exc_info.value).lower()

    def test_edge_to_must_reference_existing_node(self):
        """Edges must reference valid node slugs."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowDefinition(
                name='Test',
                nodes={
                    'n': {
                        'type': 'plain_txt_input',
                        'execution_mode': 'INPUT',
                        'config': {},
                    }
                },
                edges=[{'from': 'n', 'to': 'nonexistent'}],
                entry='n',
                exit='n',
            )
        assert 'nonexistent' in str(exc_info.value).lower()

    def test_nodes_are_parsed_as_node_definitions(self):
        """Nodes dict values should be parsed into NodeDefinition objects."""
        wf = WorkflowDefinition(
            name='Test',
            nodes={
                'llm': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'label': 'Analyze',
                    'config': {'model': 'test-model', 'template': 'test'},
                },
            },
            edges=[],
            entry='llm',
            exit='llm',
        )
        from workflow_models.wdf.nodes import NodeDefinition

        assert isinstance(wf.nodes['llm'], NodeDefinition)
        assert wf.nodes['llm'].type == 'llm_call'

    def test_edges_are_parsed_as_edge_definitions(self):
        """Edge dicts should be parsed into EdgeDefinition objects."""
        wf = WorkflowDefinition(
            name='Test',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'agent',
                    'execution_mode': 'MESSAGES',
                    'config': {},
                },
            },
            edges=[{'from': 'a', 'to': 'b'}],
            entry='a',
            exit='b',
        )
        assert isinstance(wf.edges[0], EdgeDefinition)

    def test_empty_nodes_raises(self):
        """At least one node is required."""
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name='Test',
                nodes={},
                edges=[],
                entry='n',
                exit='n',
            )

    def test_description_defaults_to_none(self):
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        assert wf.description is None

    def test_tags_defaults_to_empty_list(self):
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        assert wf.tags == []

    def test_serialization_roundtrip(self):
        """Serialize to dict and back — data should be preserved."""
        wf = WorkflowDefinition(
            name='Invoice Processing',
            description='Extract data from invoices',
            version=1,
            tags=['finance'],
            state_schema={'inputs': {}, 'outputs': {}},
            nodes={
                'upload': {
                    'type': 'file_upload',
                    'execution_mode': 'INPUT',
                    'label': 'Upload Invoice',
                    'config': {
                        'acceptedFormats': ['pdf', 'png'],
                        'maxFileSize': 10485760,
                    },
                },
                'extract': {
                    'type': 'document_extraction',
                    'execution_mode': 'FLOW',
                    'label': 'Extract Fields',
                    'config': {
                        'fields': [
                            {'name': 'vendor', 'type': 'string', 'required': True},
                        ],
                    },
                },
            },
            edges=[{'from': 'upload', 'to': 'extract'}],
            entry='upload',
            exit='extract',
        )
        data = wf.model_dump(by_alias=True)
        wf2 = WorkflowDefinition.model_validate(data)
        assert wf2.name == wf.name
        assert wf2.description == wf.description
        assert wf2.version == wf.version
        assert wf2.tags == wf.tags
        assert wf2.state_schema == wf.state_schema
        assert wf2.entry == wf.entry
        assert wf2.exit == wf.exit
        assert len(wf2.nodes) == len(wf.nodes)
        assert len(wf2.edges) == len(wf.edges)

    def test_complex_workflow_with_conditional_edges(self):
        """A workflow with static and conditional edges."""
        wf = WorkflowDefinition(
            name='Complex Workflow',
            nodes={
                'input': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'config': {},
                },
                'classify': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'classify'},
                },
                'approve': {
                    'type': 'human_review',
                    'execution_mode': 'FLOW',
                    'config': {},
                },
                'reject': {
                    'type': 'human_review',
                    'execution_mode': 'FLOW',
                    'config': {},
                },
            },
            edges=[
                {'from': 'input', 'to': 'classify'},
                {
                    'from': 'classify',
                    'to': 'approve',
                    'type': 'CONDITIONAL',
                    'condition': "output == 'yes'",
                },
                {
                    'from': 'classify',
                    'to': 'reject',
                    'type': 'CONDITIONAL',
                    'condition': "output == 'no'",
                },
            ],
            entry='input',
            exit='approve',
        )
        assert len(wf.edges) == 3
        assert wf.edges[1].condition == "output == 'yes'"
        assert wf.edges[1].type == 'CONDITIONAL'
