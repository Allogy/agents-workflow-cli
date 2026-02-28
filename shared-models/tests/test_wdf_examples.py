"""Tests that verify example YAML files are valid WDF definitions.

Ensures the example files in examples/ are parseable and pass validation.
This catches drift between the schema and the documentation examples.
"""

from pathlib import Path

import pytest
import yaml

from workflow_models.wdf.workflow import WorkflowDefinition

EXAMPLES_DIR = Path(__file__).parent.parent / 'examples'


def get_example_files():
    """Find all .workflow.yaml files in the examples directory."""
    return sorted(EXAMPLES_DIR.glob('*.workflow.yaml'))


class TestExampleFiles:
    @pytest.fixture(params=get_example_files(), ids=lambda p: p.name)
    def example_path(self, request):
        return request.param

    def test_example_is_valid_yaml(self, example_path: Path):
        """Example file must be valid YAML."""
        data = yaml.safe_load(example_path.read_text())
        assert isinstance(data, dict)

    def test_example_passes_schema_validation(self, example_path: Path):
        """Example file must pass WorkflowDefinition validation."""
        data = yaml.safe_load(example_path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert wf.name  # name is required
        assert wf.nodes  # at least one node
        assert wf.entry in wf.nodes
        assert wf.exit in wf.nodes

    def test_example_nodes_have_execution_mode(self, example_path: Path):
        """Every node must have an execution_mode field."""
        data = yaml.safe_load(example_path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        for slug, node in wf.nodes.items():
            assert node.execution_mode in (
                'INPUT',
                'OUTPUT',
                'MESSAGES',
                'FLOW',
            ), f'Node {slug!r} has invalid execution_mode: {node.execution_mode!r}'


class TestInvoiceProcessingExample:
    def test_has_4_nodes(self):
        path = EXAMPLES_DIR / 'invoice-processing.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {'upload', 'extract', 'classify', 'review'}


class TestAllNodeTypesExample:
    def test_has_9_nodes(self):
        """The all-node-types example must exercise all 9 CLI-supported node types."""
        path = EXAMPLES_DIR / 'all-node-types.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 9
        node_types = {n.type for n in wf.nodes.values()}
        # document_extraction is not included — it is not supported by the CLI
        expected_types = {
            'plain_txt_input',
            'structured_input',
            'file_upload',
            'agent',
            'rag_agent',
            'llm_call',
            'structured_output',
            'retrieve',
            'human_review',
        }
        assert node_types == expected_types


class TestLinearPipelineExample:
    """Tests for the backend-aligned linear pipeline example."""

    def test_has_3_nodes(self):
        path = EXAMPLES_DIR / 'linear-pipeline.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 3
        assert set(wf.nodes.keys()) == {'user_input', 'summarizer', 'output'}

    def test_node_types_match_backend(self):
        path = EXAMPLES_DIR / 'linear-pipeline.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert wf.nodes['user_input'].type == 'plain_txt_input'
        assert wf.nodes['user_input'].execution_mode == 'INPUT'
        assert wf.nodes['summarizer'].type == 'llm_call'
        assert wf.nodes['summarizer'].execution_mode == 'MESSAGES'
        assert wf.nodes['output'].type == 'structured_output'
        assert wf.nodes['output'].execution_mode == 'OUTPUT'

    def test_has_variable_reference(self):
        """The LLM node should reference the input node's output."""
        path = EXAMPLES_DIR / 'linear-pipeline.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        from workflow_models.wdf.variable_ref import extract_variable_refs

        refs = extract_variable_refs(wf.nodes['summarizer'].config)
        assert len(refs) == 1
        assert refs[0].slug == 'user_input'


class TestRagWorkflowExample:
    """Tests for the backend-aligned RAG workflow example."""

    def test_has_4_nodes(self):
        path = EXAMPLES_DIR / 'rag-workflow.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {'form_input', 'file_upload', 'summarizer', 'rag_agent'}


class TestAgentReviewExample:
    """Tests for the backend-aligned agent review example."""

    def test_has_4_nodes(self):
        path = EXAMPLES_DIR / 'agent-review.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {'user_input', 'agent', 'review', 'output'}

    def test_agent_config_aligned(self):
        """Agent config should have model and system_prompt."""
        path = EXAMPLES_DIR / 'agent-review.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        agent = wf.nodes['agent']
        assert agent.parsed_config is not None
        from workflow_models.wdf.nodes import AgentConfig

        assert isinstance(agent.parsed_config, AgentConfig)
        assert agent.parsed_config.model == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert 'helpful assistant' in agent.parsed_config.system_prompt


class TestRetrievalPipelineExample:
    """Tests for the backend-aligned retrieval pipeline example."""

    def test_has_5_nodes(self):
        path = EXAMPLES_DIR / 'retrieval-pipeline.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 5
        assert set(wf.nodes.keys()) == {
            'search_form',
            'vector_search',
            'content_extractor',
            'summarizer',
            'search_results',
        }

    def test_retrieve_config_aligned(self):
        """Retrieve config should have the new fields."""
        path = EXAMPLES_DIR / 'retrieval-pipeline.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        retrieve = wf.nodes['vector_search']
        assert retrieve.parsed_config is not None
        from workflow_models.wdf.nodes import RetrieveConfig

        assert isinstance(retrieve.parsed_config, RetrieveConfig)
        assert retrieve.parsed_config.enableReranking is False
        assert retrieve.parsed_config.includeMetadata is True
