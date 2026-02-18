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
    """EdgeDefinition: from (slug), to (slug), type, condition."""

    def test_static_edge_minimal(self):
        """Static edges only need from and to."""
        edge = EdgeDefinition(**{'from': 'upload', 'to': 'extract'})
        assert edge.from_node == 'upload'
        assert edge.to == 'extract'
        assert edge.type is None or edge.type == 'static'

    def test_conditional_edge(self):
        edge = EdgeDefinition(
            **{
                'from': 'classify',
                'to': 'approve',
                'condition': "output.category == 'SUPPLIES'",
            }
        )
        assert edge.from_node == 'classify'
        assert edge.to == 'approve'
        assert edge.condition == "output.category == 'SUPPLIES'"

    def test_edge_with_explicit_type(self):
        edge = EdgeDefinition(
            **{
                'from': 'iterator',
                'to': 'process_item',
                'type': 'mapping',
            }
        )
        assert edge.type == 'mapping'

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
                    'label': 'Enter Question',
                    'config': {},
                },
                'agent': {
                    'type': 'agent',
                    'label': 'Process',
                    'config': {'agentId': 'test-agent'},
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
            tags=['finance', 'document-processing'],
            nodes={
                'start': {
                    'type': 'plain_txt_input',
                    'config': {},
                },
            },
            edges=[],
            entry='start',
            exit='start',
        )
        assert wf.description == 'A complete workflow with all fields'
        assert wf.tags == ['finance', 'document-processing']

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
                edges=[],
                exit='n',
            )  # type: ignore[call-arg]

    def test_missing_exit_raises(self):
        with pytest.raises(ValidationError):
            WorkflowDefinition(
                name='Test',
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
                edges=[],
                entry='n',
            )  # type: ignore[call-arg]

    def test_entry_must_reference_existing_node(self):
        """Entry point must be a key in the nodes dict."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowDefinition(
                name='Test',
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
                nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
                'a': {'type': 'plain_txt_input', 'config': {}},
                'b': {'type': 'agent', 'config': {'agentId': 'test'}},
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
            nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        assert wf.description is None

    def test_tags_defaults_to_empty_list(self):
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'config': {}}},
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
            tags=['finance'],
            nodes={
                'upload': {
                    'type': 'file_upload',
                    'label': 'Upload Invoice',
                    'config': {
                        'acceptedFormats': ['pdf', 'png'],
                        'maxFileSize': 10485760,
                    },
                },
                'extract': {
                    'type': 'document_extraction',
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
        assert wf2.tags == wf.tags
        assert wf2.entry == wf.entry
        assert wf2.exit == wf.exit
        assert len(wf2.nodes) == len(wf.nodes)
        assert len(wf2.edges) == len(wf.edges)

    def test_complex_workflow_with_all_edge_types(self):
        """A workflow with static and conditional edges."""
        wf = WorkflowDefinition(
            name='Complex Workflow',
            nodes={
                'input': {'type': 'plain_txt_input', 'config': {}},
                'classify': {
                    'type': 'llm_call',
                    'config': {'model': 'test', 'template': 'classify'},
                },
                'approve': {'type': 'human_review', 'config': {}},
                'reject': {'type': 'human_review', 'config': {}},
            },
            edges=[
                {'from': 'input', 'to': 'classify'},
                {'from': 'classify', 'to': 'approve', 'condition': "output == 'yes'"},
                {'from': 'classify', 'to': 'reject', 'condition': "output == 'no'"},
            ],
            entry='input',
            exit='approve',
        )
        assert len(wf.edges) == 3
        assert wf.edges[1].condition == "output == 'yes'"
