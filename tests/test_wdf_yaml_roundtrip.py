"""Tests for WDF YAML round-trip serialization.

BDD Scenario from RAG-945:
  Given a workflow YAML file
  When it is parsed to Pydantic models and serialized back to YAML
  Then the output is semantically identical to the input

YAML handling lives in the CLI layer (pyyaml is a CLI dependency,
not a shared-models dependency). These tests verify that workflow
YAML files can be loaded, validated, and written back without data loss.
"""

import yaml
from workflow_models.wdf.workflow import WorkflowDefinition

# Helper to import the CLI-layer YAML helpers
from cli.wdf_yaml import dump_workflow_yaml, load_workflow_yaml


class TestLoadWorkflowYaml:
    """Parse YAML string into WorkflowDefinition."""

    def test_simple_workflow(self):
        yaml_str = """\
name: Simple Q&A
description: Ask a question, get an answer
tags: [qa]
nodes:
  question:
    type: plain_txt_input
    execution_mode: INPUT
    label: Ask Question
    config:
      placeholder: "What would you like to know?"
  answer:
    type: agent
    execution_mode: MESSAGES
    label: Answer Agent
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      system_prompt: You are a helpful Q&A assistant.
edges:
  - from: question
    to: answer
entry: question
exit: answer
"""
        wf = load_workflow_yaml(yaml_str)
        assert isinstance(wf, WorkflowDefinition)
        assert wf.name == 'Simple Q&A'
        assert wf.description == 'Ask a question, get an answer'
        assert wf.tags == ['qa']
        assert len(wf.nodes) == 2
        assert len(wf.edges) == 1
        assert wf.entry == 'question'
        assert wf.exit == 'answer'

    def test_complex_workflow_with_all_node_types(self):
        """A workflow exercising multiple node types."""
        yaml_str = """\
name: Document Processing Pipeline
nodes:
  upload:
    type: file_upload
    execution_mode: INPUT
    label: Upload Document
    config:
      acceptedFormats: [pdf, png, jpg]
      maxFileSize: 10485760
      textExtraction: automatic
  extract:
    type: document_extraction
    execution_mode: FLOW
    label: Extract Fields
    config:
      extractionMethod: llm
      fields:
        - name: vendor_name
          type: string
          required: true
        - name: total_amount
          type: number
          required: true
      prompt: "Extract vendor and total from invoice."
  classify:
    type: llm_call
    execution_mode: MESSAGES
    label: Classify Document
    config:
      model: anthropic.claude-sonnet-4-5-v2
      temperature: 0.0
      maxTokens: 200
      template: |
        Classify: {{extract.output.extractedData}}
  review:
    type: human_review
    execution_mode: FLOW
    label: Manager Approval
    config:
      review_prompt: "Review the extracted data."
      timeoutMinutes: 1440
edges:
  - from: upload
    to: extract
  - from: extract
    to: classify
  - from: classify
    to: review
entry: upload
exit: review
"""
        wf = load_workflow_yaml(yaml_str)
        assert len(wf.nodes) == 4
        assert wf.nodes['upload'].type == 'file_upload'
        assert wf.nodes['extract'].type == 'document_extraction'
        assert wf.nodes['classify'].type == 'llm_call'
        assert wf.nodes['review'].type == 'human_review'


class TestDumpWorkflowYaml:
    """Serialize WorkflowDefinition back to YAML string."""

    def test_dump_produces_valid_yaml(self):
        wf = WorkflowDefinition(
            name='Test Workflow',
            description='A test',
            tags=['test'],
            nodes={
                'start': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'config': {},
                },
                'end': {
                    'type': 'agent',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'system_prompt': 'test'},
                },
            },
            edges=[{'from': 'start', 'to': 'end'}],
            entry='start',
            exit='end',
        )
        yaml_str = dump_workflow_yaml(wf)
        # The output should be valid YAML
        data = yaml.safe_load(yaml_str)
        assert data['name'] == 'Test Workflow'
        assert data['description'] == 'A test'
        assert 'start' in data['nodes']
        assert 'end' in data['nodes']

    def test_dump_uses_from_key_not_from_node(self):
        """YAML output should use 'from' (the alias), not 'from_node'."""
        wf = WorkflowDefinition(
            name='Test',
            nodes={
                'a': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'config': {},
                },
                'b': {
                    'type': 'agent',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'system_prompt': 'test'},
                },
            },
            edges=[{'from': 'a', 'to': 'b'}],
            entry='a',
            exit='b',
        )
        yaml_str = dump_workflow_yaml(wf)
        data = yaml.safe_load(yaml_str)
        assert 'from' in data['edges'][0]
        assert 'from_node' not in data['edges'][0]

    def test_dump_excludes_none_values(self):
        """None/null values should not appear in YAML output."""
        wf = WorkflowDefinition(
            name='Test',
            nodes={'n': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}}},
            edges=[],
            entry='n',
            exit='n',
        )
        yaml_str = dump_workflow_yaml(wf)
        assert 'description' not in yaml_str or 'null' not in yaml_str
        data = yaml.safe_load(yaml_str)
        assert 'description' not in data


class TestYamlRoundTrip:
    """Parse YAML -> model -> serialize -> parse again: data must match."""

    def test_roundtrip_preserves_data(self):
        """Core BDD scenario: round-trip preserves semantic data."""
        original_yaml = """\
name: Invoice Processing
description: Extract data from uploaded invoices
version: 1
tags:
  - finance
  - document-processing
nodes:
  upload:
    type: file_upload
    execution_mode: INPUT
    label: Upload Invoice
    config:
      acceptedFormats:
        - pdf
        - png
      maxFileSize: 10485760
      textExtraction: automatic
  extract:
    type: document_extraction
    execution_mode: FLOW
    label: Extract Fields
    config:
      fields:
        - name: vendor
          type: string
          required: true
        - name: amount
          type: number
          required: true
  review:
    type: human_review
    execution_mode: FLOW
    label: Approve
    config:
      review_prompt: Review and approve.
      timeoutMinutes: 60
edges:
  - from: upload
    to: extract
  - from: extract
    to: review
entry: upload
exit: review
"""
        # Parse -> model
        wf = load_workflow_yaml(original_yaml)
        # Model -> YAML
        output_yaml = dump_workflow_yaml(wf)
        # YAML -> model again
        wf2 = load_workflow_yaml(output_yaml)

        # Semantic comparison
        assert wf2.name == wf.name
        assert wf2.description == wf.description
        assert wf2.version == wf.version
        assert wf2.tags == wf.tags
        assert wf2.entry == wf.entry
        assert wf2.exit == wf.exit
        assert set(wf2.nodes.keys()) == set(wf.nodes.keys())
        assert len(wf2.edges) == len(wf.edges)

        # Deep node comparison
        for slug in wf.nodes:
            assert wf2.nodes[slug].type == wf.nodes[slug].type
            assert wf2.nodes[slug].execution_mode == wf.nodes[slug].execution_mode
            assert wf2.nodes[slug].label == wf.nodes[slug].label
            assert wf2.nodes[slug].config == wf.nodes[slug].config

        # Deep edge comparison
        for e1, e2 in zip(wf.edges, wf2.edges, strict=True):
            assert e1.from_node == e2.from_node
            assert e1.to == e2.to

    def test_roundtrip_with_conditional_edges(self):
        original_yaml = """\
name: Conditional Workflow
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  yes_path:
    type: agent
    execution_mode: MESSAGES
    config:
      model: test
      system_prompt: Handle yes
  no_path:
    type: agent
    execution_mode: MESSAGES
    config:
      model: test
      system_prompt: Handle no
edges:
  - from: input
    to: yes_path
    type: CONDITIONAL
    condition: "output == 'yes'"
  - from: input
    to: no_path
    type: CONDITIONAL
    condition: "output == 'no'"
entry: input
exit: yes_path
"""
        wf = load_workflow_yaml(original_yaml)
        output_yaml = dump_workflow_yaml(wf)
        wf2 = load_workflow_yaml(output_yaml)

        assert len(wf2.edges) == 2
        assert wf2.edges[0].condition == "output == 'yes'"
        assert wf2.edges[1].condition == "output == 'no'"

    def test_roundtrip_with_variable_references(self):
        """Variable references in templates survive the round-trip."""
        original_yaml = """\
name: Var Ref Workflow
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  llm:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test-model
      template: "Process: {{input.output.text}}"
edges:
  - from: input
    to: llm
entry: input
exit: llm
"""
        wf = load_workflow_yaml(original_yaml)
        output_yaml = dump_workflow_yaml(wf)
        wf2 = load_workflow_yaml(output_yaml)

        assert wf2.nodes['llm'].config['template'] == 'Process: {{input.output.text}}'
