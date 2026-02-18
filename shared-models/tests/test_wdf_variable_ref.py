"""Tests for WDF variable reference extraction.

BDD Scenario from RAG-945:
  Given a node config contains "slug.output.field"
  When the schema parses it
  Then the variable reference is identified for later validation

Variable references use the {{slug.output.field}} Mustache-style syntax.
The extractor should find all references in config strings, including
nested structures and multi-line templates.
"""

import pytest

from workflow_models.wdf.variable_ref import VariableRef, extract_variable_refs


class TestVariableRef:
    """VariableRef data class for parsed references."""

    def test_basic_ref(self):
        ref = VariableRef(slug='extract', path='output.extractedData')
        assert ref.slug == 'extract'
        assert ref.path == 'output.extractedData'
        assert ref.raw == 'extract.output.extractedData'

    def test_simple_ref(self):
        ref = VariableRef(slug='input', path='output.data')
        assert ref.raw == 'input.output.data'


class TestExtractVariableRefs:
    """extract_variable_refs finds all {{...}} patterns in config values."""

    def test_single_ref_in_string(self):
        refs = extract_variable_refs('Analyze: {{extract.output.extractedData}}')
        assert len(refs) == 1
        assert refs[0].slug == 'extract'
        assert refs[0].path == 'output.extractedData'

    def test_multiple_refs_in_string(self):
        text = 'Compare {{a.output.x}} with {{b.output.y}}'
        refs = extract_variable_refs(text)
        assert len(refs) == 2
        assert refs[0].slug == 'a'
        assert refs[1].slug == 'b'

    def test_no_refs(self):
        refs = extract_variable_refs('Just a plain string with no references')
        assert refs == []

    def test_empty_string(self):
        refs = extract_variable_refs('')
        assert refs == []

    def test_multiline_template(self):
        template = """Given the following invoice data:
{{extract.output.extractedData}}

Classify this invoice into one of: SUPPLIES, SERVICES, EQUIPMENT, OTHER.
Use context from {{context.output.hints}} if available."""
        refs = extract_variable_refs(template)
        assert len(refs) == 2
        assert refs[0].slug == 'extract'
        assert refs[1].slug == 'context'

    def test_ref_in_dict(self):
        """Should find refs in nested dict values."""
        config = {
            'template': 'Process: {{input.output.data}}',
            'model': 'anthropic.claude-sonnet-4-5-v2',
        }
        refs = extract_variable_refs(config)
        assert len(refs) == 1
        assert refs[0].slug == 'input'

    def test_ref_in_nested_dict(self):
        """Should traverse nested dicts."""
        config = {
            'outer': {
                'inner': 'Value: {{deep.output.field}}',
            }
        }
        refs = extract_variable_refs(config)
        assert len(refs) == 1
        assert refs[0].slug == 'deep'

    def test_ref_in_list(self):
        """Should traverse lists."""
        config = ['First: {{a.output.x}}', 'Second: {{b.output.y}}']
        refs = extract_variable_refs(config)
        assert len(refs) == 2

    def test_non_string_values_skipped(self):
        """Numbers, booleans, None should not cause errors."""
        config = {'count': 5, 'enabled': True, 'data': None}
        refs = extract_variable_refs(config)
        assert refs == []

    def test_deduplication(self):
        """Same reference appearing multiple times should not be deduplicated
        (caller can do that if needed)."""
        text = '{{a.output.x}} and {{a.output.x}} again'
        refs = extract_variable_refs(text)
        assert len(refs) == 2

    def test_complex_path(self):
        """References can have multiple dot-separated segments."""
        refs = extract_variable_refs('{{node.output.nested.deep.field}}')
        assert len(refs) == 1
        assert refs[0].slug == 'node'
        assert refs[0].path == 'output.nested.deep.field'

    def test_underscore_and_hyphen_in_slug(self):
        """Slugs can contain underscores and hyphens."""
        refs = extract_variable_refs('{{my_node.output.data}}')
        assert len(refs) == 1
        assert refs[0].slug == 'my_node'

        refs = extract_variable_refs('{{my-node.output.data}}')
        assert len(refs) == 1
        assert refs[0].slug == 'my-node'

    def test_extract_from_workflow_definition(self):
        """Integration: extract refs from a full workflow's node configs."""
        from workflow_models.wdf.workflow import WorkflowDefinition

        wf = WorkflowDefinition(
            name='Test',
            nodes={
                'input': {'type': 'plain_txt_input', 'config': {}},
                'classify': {
                    'type': 'llm_call',
                    'config': {
                        'model': 'test',
                        'template': 'Classify: {{input.output.text}}',
                    },
                },
            },
            edges=[{'from': 'input', 'to': 'classify'}],
            entry='input',
            exit='classify',
        )
        # Extract refs from the classify node's config
        refs = extract_variable_refs(wf.nodes['classify'].config)
        assert len(refs) == 1
        assert refs[0].slug == 'input'
        assert refs[0].path == 'output.text'
