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

    def test_invoice_processing_has_4_nodes(self):
        """Specific check for the invoice processing example."""
        path = EXAMPLES_DIR / 'invoice-processing.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 4
        assert set(wf.nodes.keys()) == {'upload', 'extract', 'classify', 'review'}

    def test_all_node_types_has_10_nodes(self):
        """The all-node-types example must exercise all 10 node types."""
        path = EXAMPLES_DIR / 'all-node-types.workflow.yaml'
        data = yaml.safe_load(path.read_text())
        wf = WorkflowDefinition.model_validate(data)
        assert len(wf.nodes) == 10
        node_types = {n.type for n in wf.nodes.values()}
        expected_types = {
            'plain_txt_input',
            'structured_input',
            'file_upload',
            'agent',
            'rag_agent',
            'llm_call',
            'structured_output',
            'retrieve',
            'document_extraction',
            'human_review',
        }
        assert node_types == expected_types
