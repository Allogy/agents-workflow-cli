"""Unit tests for push command (TDD Red phase)."""

from __future__ import annotations

from uuid import UUID

import pytest
from workflow_models.wdf import EdgeDefinition, NodeDefinition, WorkflowDefinition

from cli.commands.push import (
    DependencyResolutionError,
    PushError,
    _is_uuid,
    build_node_parameters,
    generate_node_layout,
    resolve_dependencies,
    wdf_to_api_payload,
)

# --- Test Fixtures ---


@pytest.fixture
def simple_workflow() -> WorkflowDefinition:
    """Simple 3-node workflow for testing."""
    return WorkflowDefinition(
        name='Test Workflow',
        description='A simple test workflow',
        version=1,
        nodes={
            'input': NodeDefinition(
                type='plain_txt_input',
                execution_mode='INPUT',
                label='User Input',
                config={'placeholder': 'Enter text'},
            ),
            'llm': NodeDefinition(
                type='llm_call',
                execution_mode='MESSAGES',
                label='LLM Call',
                config={
                    'model': 'anthropic.claude-3-5-sonnet-20241022-v2:0',
                    'system_prompt': 'You are helpful',
                    'temperature': 0.7,
                    'maxTokens': 1024,
                    'template': '{{input.output.text}}',
                },
            ),
            'output': NodeDefinition(
                type='structured_output',
                execution_mode='OUTPUT',
                label='Output',
                config={'schema': {'type': 'object', 'properties': {}}},
            ),
        },
        edges=[
            EdgeDefinition.model_validate({'from': 'input', 'to': 'llm'}),
            EdgeDefinition.model_validate({'from': 'llm', 'to': 'output'}),
        ],
        entry='input',
        exit='output',
    )


@pytest.fixture
def workflow_with_agent() -> WorkflowDefinition:
    """Workflow with agent reference.

    Uses model_construct() to bypass validation for the agent node,
    allowing us to use agent_name (which is resolved before validation).
    """
    return WorkflowDefinition.model_construct(
        name='Agent Workflow',
        description='Workflow using an agent',
        version=1,
        nodes={
            'input': NodeDefinition(
                type='plain_txt_input',
                execution_mode='INPUT',
                label='User Input',
                config={'placeholder': 'Enter text'},
            ),
            'agent': NodeDefinition.model_construct(
                type='agent',
                execution_mode='MESSAGES',
                label='My Agent',
                config={
                    'agent_name': 'Test Agent',
                    'input_text': '{{input.output.text}}',
                },
            ),
            'output': NodeDefinition(
                type='structured_output',
                execution_mode='OUTPUT',
                label='Output',
                config={'schema': {'type': 'object', 'properties': {}}},
            ),
        },
        edges=[
            EdgeDefinition.model_validate({'from': 'input', 'to': 'agent'}),
            EdgeDefinition.model_validate({'from': 'agent', 'to': 'output'}),
        ],
        entry='input',
        exit='output',
    )


@pytest.fixture
def workflow_with_kb() -> WorkflowDefinition:
    """Workflow with knowledge base reference.

    Uses model_construct() to bypass validation for the retrieve node,
    allowing us to use knowledge_base_name (which is resolved before validation).
    """
    return WorkflowDefinition.model_construct(
        name='RAG Workflow',
        description='Workflow using a knowledge base',
        version=1,
        nodes={
            'input': NodeDefinition(
                type='plain_txt_input',
                execution_mode='INPUT',
                label='User Input',
                config={'placeholder': 'Enter question'},
            ),
            'retrieval': NodeDefinition.model_construct(
                type='retrieve',
                execution_mode='FLOW',
                label='KB Retrieval',
                config={
                    'knowledge_base_name': 'Test KB',
                    'query': '{{input.output.text}}',
                    'topK': 5,
                },
            ),
            'output': NodeDefinition(
                type='structured_output',
                execution_mode='OUTPUT',
                label='Output',
                config={'schema': {'type': 'object', 'properties': {}}},
            ),
        },
        edges=[
            EdgeDefinition.model_validate({'from': 'input', 'to': 'retrieval'}),
            EdgeDefinition.model_validate({'from': 'retrieval', 'to': 'output'}),
        ],
        entry='input',
        exit='output',
    )


@pytest.fixture
def mock_client():
    """Mock WorkflowClient."""
    from unittest.mock import MagicMock

    return MagicMock()


# --- Dependency Resolution Tests ---


def test_resolve_dependencies_no_deps(simple_workflow, mock_client):
    """Test resolving workflow with no dependencies."""
    resolved = resolve_dependencies(simple_workflow, mock_client)
    assert resolved == {}
    mock_client.find_agent_by_name.assert_not_called()
    mock_client.find_knowledge_base_by_name.assert_not_called()


def test_resolve_dependencies_with_agent(workflow_with_agent, mock_client):
    """Test resolving workflow with agent reference."""
    mock_client.find_agent_by_name.return_value = {
        'id': '12345678-1234-1234-1234-123456789012',
        'name': 'Test Agent',
    }

    resolved = resolve_dependencies(workflow_with_agent, mock_client)

    assert 'agent:Test Agent' in resolved
    assert resolved['agent:Test Agent'] == UUID('12345678-1234-1234-1234-123456789012')
    mock_client.find_agent_by_name.assert_called_once_with('Test Agent')


def test_resolve_dependencies_with_kb(workflow_with_kb, mock_client):
    """Test resolving workflow with knowledge base reference."""
    mock_client.find_knowledge_base_by_name.return_value = {
        'id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        'name': 'Test KB',
    }

    resolved = resolve_dependencies(workflow_with_kb, mock_client)

    assert 'kb:Test KB' in resolved
    assert resolved['kb:Test KB'] == UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    mock_client.find_knowledge_base_by_name.assert_called_once_with('Test KB')


def test_resolve_dependencies_agent_not_found(workflow_with_agent, mock_client):
    """Test error when agent cannot be resolved."""
    mock_client.find_agent_by_name.return_value = None
    mock_client.list_agents.return_value = []

    with pytest.raises(DependencyResolutionError, match="Cannot resolve agent 'Test Agent'"):
        resolve_dependencies(workflow_with_agent, mock_client)


def test_resolve_dependencies_kb_not_found(workflow_with_kb, mock_client):
    """Test error when knowledge base cannot be resolved."""
    mock_client.find_knowledge_base_by_name.return_value = None
    mock_client.list_knowledge_bases.return_value = []

    with pytest.raises(DependencyResolutionError, match="Cannot resolve knowledge base 'Test KB'"):
        resolve_dependencies(workflow_with_kb, mock_client)


# --- Layout Generation Tests ---


def test_generate_node_layout_simple(simple_workflow):
    """Test layout generation for simple workflow."""
    layout = generate_node_layout(simple_workflow)

    assert len(layout) == 3
    assert 'input' in layout
    assert 'llm' in layout
    assert 'output' in layout

    # Verify positions are tuples of (x, y)
    for pos in layout.values():
        assert isinstance(pos, tuple)
        assert len(pos) == 2
        assert isinstance(pos[0], int)
        assert isinstance(pos[1], int)


def test_generate_node_layout_vertical_spacing(simple_workflow):
    """Test that nodes are spaced vertically."""
    layout = generate_node_layout(simple_workflow)

    # Y positions should be different
    y_positions = [pos[1] for pos in layout.values()]
    assert len(set(y_positions)) == 3  # All unique


def test_generate_node_layout_empty_workflow():
    """Test layout generation for workflow with no nodes."""
    workflow = WorkflowDefinition.model_construct(
        name='Empty',
        description='Empty workflow',
        version=1,
        nodes={},
        edges=[],
        entry=None,
        exit=None,
    )
    layout = generate_node_layout(workflow)
    assert layout == {}


# --- WDF to API Payload Conversion Tests ---


def test_wdf_to_api_payload_create_mode(simple_workflow):
    """Test payload conversion for create mode (no workflow_id)."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert 'workflow' in payload
    assert payload['workflow']['version'] == 1
    assert payload['workflow']['organization_id'] == str(org_id)
    assert payload['workflow_id'] is None  # Create mode

    # Name and description are now in metadata
    assert 'metadata' in payload
    assert payload['metadata']['name'] == 'Test Workflow'
    assert payload['metadata']['description'] == 'A simple test workflow'

    assert 'nodes' in payload
    assert 'edges' in payload
    assert 'node_inputs' in payload
    assert 'node_outputs' in payload
    assert 'node_visuals' in payload
    assert 'edge_visuals' in payload


def test_wdf_to_api_payload_update_mode(simple_workflow):
    """Test payload conversion for update mode (with workflow_id)."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    workflow_id = UUID('11111111-1111-1111-1111-111111111111')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id, workflow_id)

    assert payload['workflow_id'] == str(workflow_id)


def test_wdf_to_api_payload_includes_nodes(simple_workflow):
    """Test that payload includes all nodes."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['nodes']) == 3
    # Check that nodes have required fields
    for node in payload['nodes']:
        assert 'id' in node
        assert 'config_type' in node
        assert 'execution_mode' in node
        assert 'config' in node


def test_wdf_to_api_payload_includes_edges(simple_workflow):
    """Test that payload includes all edges."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['edges']) == 2
    # Check that edges have required fields
    for edge in payload['edges']:
        assert 'source_node_id' in edge
        assert 'target_node_id' in edge
        assert 'edge_type' in edge


def test_wdf_to_api_payload_includes_visuals(simple_workflow):
    """Test that payload includes node visuals with positions."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['node_visuals']) == 3
    # Check that visuals have positions
    for visual in payload['node_visuals']:
        assert 'node_id' in visual
        assert 'position_x' in visual
        assert 'position_y' in visual


def test_wdf_to_api_payload_resolves_agent_refs(workflow_with_agent):
    """Test that agent references are resolved to UUIDs."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(workflow_with_agent)
    agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
    resolved_deps = {'agent:Test Agent': agent_uuid}

    payload, slug_to_uuid = wdf_to_api_payload(workflow_with_agent, resolved_deps, layout, org_id)

    # Find the agent node by its UUID
    agent_node_uuid = slug_to_uuid['agent']
    agent_node = next(n for n in payload['nodes'] if n['id'] == str(agent_node_uuid))
    # agent_id should be in parameters as agentId (for frontend/runtime)
    assert agent_node['parameters']['agentId'] == str(agent_uuid)
    assert 'agent_name' not in agent_node['config']  # Name should be replaced
    assert 'agent_name' not in agent_node['parameters']  # Name should be replaced


def test_wdf_to_api_payload_resolves_kb_refs(workflow_with_kb):
    """Test that knowledge base references are resolved to UUIDs."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(workflow_with_kb)
    kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    resolved_deps = {'kb:Test KB': kb_uuid}

    payload, slug_to_uuid = wdf_to_api_payload(workflow_with_kb, resolved_deps, layout, org_id)

    # Find the retrieval node by its UUID
    retrieval_node_uuid = slug_to_uuid['retrieval']
    retrieval_node = next(n for n in payload['nodes'] if n['id'] == str(retrieval_node_uuid))
    # knowledge_base_id should be in parameters as knowledgeBaseId (list, for frontend/runtime)
    assert str(kb_uuid) in retrieval_node['parameters']['knowledgeBaseId']
    assert 'knowledge_base_name' not in retrieval_node['config']  # Name should be replaced
    assert 'knowledge_base_name' not in retrieval_node['parameters']  # Name should be replaced


def test_wdf_to_api_payload_generates_io_for_nodes(simple_workflow):
    """Test that node inputs and outputs are generated."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    # Nodes should have inputs/outputs based on their type
    # plain_txt_input has output
    # llm_call has input and output
    # structured_output has input
    assert len(payload['node_inputs']) > 0
    assert len(payload['node_outputs']) > 0


# --- Error Handling Tests ---


def test_push_error_exception():
    """Test PushError exception."""
    with pytest.raises(PushError, match='Test error'):
        raise PushError('Test error')


def test_dependency_resolution_error_inherits_push_error():
    """Test DependencyResolutionError inherits from PushError."""
    assert issubclass(DependencyResolutionError, PushError)


# --- Edge Cases ---


def test_wdf_to_api_payload_empty_description(simple_workflow):
    """Test payload with None description."""
    simple_workflow.description = None
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert payload['metadata']['description'] == ''


def test_generate_node_layout_single_node():
    """Test layout generation for single node."""
    workflow = WorkflowDefinition(
        name='Single',
        description='Single node workflow',
        version=1,
        nodes={
            'only': NodeDefinition(
                type='plain_txt_input',
                execution_mode='INPUT',
                label='Only Node',
                config={'placeholder': 'Enter text'},
            )
        },
        edges=[],
        entry='only',
        exit='only',
    )
    layout = generate_node_layout(workflow)
    assert len(layout) == 1
    assert 'only' in layout


# --- Build Node Parameters Tests ---


class TestBuildNodeParameters:
    """Test the build_node_parameters function."""

    def test_plain_txt_input_parameters(self):
        """Test parameters for PLAIN_TXT_INPUT node."""
        node_def = NodeDefinition(
            type='plain_txt_input',
            execution_mode='INPUT',
            label='User Input',
            config={'placeholder': 'Enter text'},
        )
        slug_to_uuid: dict[str, UUID] = {}
        params = build_node_parameters(node_def, 'user-input', node_def.config, slug_to_uuid)
        assert params['type'] == 'plainTextInput'
        assert params['label'] == 'User Input'
        assert params['function_name'] == 'user_input'
        assert params['prompt'] == 'Enter text'

    def test_file_upload_parameters(self):
        """Test parameters for FILE_UPLOAD node."""
        node_def = NodeDefinition(
            type='file_upload',
            execution_mode='INPUT',
            label='Upload Doc',
            config={
                'acceptedFormats': ['pdf', 'docx'],
                'maxFileSize': 10,
                'textExtraction': 'automatic',
            },
        )
        slug_to_uuid: dict[str, UUID] = {}
        params = build_node_parameters(node_def, 'upload-doc', node_def.config, slug_to_uuid)
        assert params['type'] == 'fileUpload'
        assert params['acceptedFormats'] == ['pdf', 'docx']
        assert params['maxFileSize'] == 10
        assert params['textExtraction'] == 'automatic'
        assert params['extractText'] is True

    def test_rag_agent_parameters_with_slug_replacement(self):
        """Test parameters for RAG_AGENT node replaces slug refs with UUIDs."""
        input_uuid = UUID('11111111-1111-1111-1111-111111111111')
        slug_to_uuid = {'text-input': input_uuid}
        node_config = {
            'agent_id': str(UUID('22222222-2222-2222-2222-222222222222')),
            'knowledge_base_ids': ['33333333-3333-3333-3333-333333333333'],
            'primaryInput': '{{text-input.output.text}}',
        }
        node_def = NodeDefinition.model_construct(
            type='rag_agent',
            execution_mode='MESSAGES',
            label='RAG Agent',
            config=node_config,
        )
        params = build_node_parameters(node_def, 'rag-agent', node_config, slug_to_uuid)
        assert params['type'] == 'ragAgent'
        assert params['agentId'] == node_config['agent_id']
        assert params['knowledgeBasesOverride'] == node_config['knowledge_base_ids']
        # Slug reference should be replaced with UUID
        assert f'{{{{{str(input_uuid)}' in params['primaryInput']

    def test_llm_call_parameters_with_template(self):
        """Test parameters for LLM_CALL node preserves template."""
        slug_to_uuid: dict[str, UUID] = {}
        node_def = NodeDefinition(
            type='llm_call',
            execution_mode='MESSAGES',
            label='Summarizer',
            config={
                'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
                'template': 'Summarize: {{input.output.text}}',
                'system_prompt': 'You are a summarizer.',
                'temperature': 0.7,
                'maxTokens': 1000,
            },
        )
        params = build_node_parameters(node_def, 'summarizer', node_def.config, slug_to_uuid)
        assert params['type'] == 'llmPrompt'
        assert params['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert params['systemPrompt'] == 'You are a summarizer.'
        assert params['temperature'] == 0.7
        assert params['maxTokens'] == 1000

    def test_agent_parameters(self):
        """Test parameters for AGENT node."""
        node_config = {
            'agent_id': str(UUID('22222222-2222-2222-2222-222222222222')),
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
            'temperature': 0.7,
            'maxTokens': 2048,
        }
        node_def = NodeDefinition.model_construct(
            type='agent',
            execution_mode='MESSAGES',
            label='My Agent',
            config=node_config,
        )
        params = build_node_parameters(node_def, 'my-agent', node_config, {})
        assert params['type'] == 'agent'
        assert params['agentId'] == node_config['agent_id']
        assert params['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert params['system_prompt'] == 'You are helpful.'

    def test_common_fields_always_present(self):
        """Test that common UI fields are always present."""
        node_def = NodeDefinition(
            type='plain_txt_input',
            execution_mode='INPUT',
            label='Test',
            config={},
        )
        params = build_node_parameters(node_def, 'test-node', {}, {})
        assert params['type'] == 'plainTextInput'
        assert params['label'] == 'Test'
        assert params['function_name'] == 'test_node'
        assert params['collapsed'] is False
        assert params['validationLevel'] == 'ok'
        assert params['validationMessages'] == []

    def test_label_falls_back_to_slug(self):
        """Test that label falls back to slug when not set on NodeDefinition."""
        node_def = NodeDefinition.model_construct(
            type='plain_txt_input',
            execution_mode='INPUT',
            label=None,
            config={},
        )
        params = build_node_parameters(node_def, 'my-input', {}, {})
        assert params['label'] == 'my-input'

    def test_retrieve_search_query_slug_replaced(self):
        """Test that searchQuery slug references are replaced with UUIDs."""
        input_uuid = UUID('11111111-1111-1111-1111-111111111111')
        slug_to_uuid = {'form_input': input_uuid}
        node_config = {
            'knowledgeBaseId': ['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'],
            'topK': 5,
            'searchQuery': '{{form_input.output.formData.query}}',
        }
        node_def = NodeDefinition.model_construct(
            type='retrieve',
            execution_mode='FLOW',
            label='Search',
            config=node_config,
        )
        params = build_node_parameters(node_def, 'search', node_config, slug_to_uuid)
        # searchQuery should have slug replaced with UUID
        assert f'{{{{{str(input_uuid)}' in params['searchQuery']
        assert 'form_input' not in params['searchQuery']

    def test_retrieve_search_query_without_ref(self):
        """Test that searchQuery without variable refs is passed through."""
        node_config = {
            'knowledgeBaseId': ['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'],
            'topK': 5,
            'searchQuery': 'static query text',
        }
        node_def = NodeDefinition.model_construct(
            type='retrieve',
            execution_mode='FLOW',
            label='Search',
            config=node_config,
        )
        params = build_node_parameters(node_def, 'search', node_config, {})
        assert params['searchQuery'] == 'static query text'


class TestWdfToApiPayloadParameters:
    """Test that wdf_to_api_payload populates parameters correctly."""

    def test_nodes_have_parameters(self, simple_workflow):
        """Test that nodes in the payload have populated parameters."""
        org_id = UUID('99999999-9999-9999-9999-999999999999')
        layout = generate_node_layout(simple_workflow)
        resolved_deps: dict = {}

        payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

        for node in payload['nodes']:
            # All nodes should have non-empty parameters
            assert node['parameters'], f'Node {node["config_type"]} has empty parameters'
            # Parameters should have common fields
            assert 'type' in node['parameters']
            assert 'label' in node['parameters']
            assert 'function_name' in node['parameters']

    def test_nodes_have_function_name(self, simple_workflow):
        """Test that nodes in the payload have function_name set."""
        org_id = UUID('99999999-9999-9999-9999-999999999999')
        layout = generate_node_layout(simple_workflow)
        resolved_deps: dict = {}

        payload, _ = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

        for node in payload['nodes']:
            assert node['function_name'] is not None
            assert isinstance(node['function_name'], str)

    def test_resolve_dependencies_with_kb_names_list(self):
        """Test resolving knowledge_base_names (list) in config."""
        from unittest.mock import MagicMock

        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    config={
                        'knowledge_base_names': ['KB One', 'KB Two'],
                        'agentId': 'some-agent-id',
                    },
                ),
            },
            edges=[],
            entry='input',
            exit='rag',
        )
        mock_client = MagicMock()
        mock_client.find_knowledge_base_by_name.side_effect = [
            {'id': '11111111-1111-1111-1111-111111111111', 'name': 'KB One'},
            {'id': '22222222-2222-2222-2222-222222222222', 'name': 'KB Two'},
        ]

        resolved = resolve_dependencies(workflow, mock_client)
        assert 'kb:KB One' in resolved
        assert 'kb:KB Two' in resolved
        assert mock_client.find_knowledge_base_by_name.call_count == 2


# ============================================================================
# UUID Passthrough Tests (RAG-950: Scenario "UUID references are passed through")
# ============================================================================


class TestIsUuid:
    """Test the _is_uuid helper function."""

    def test_valid_uuid_v4(self):
        """Test that a standard UUID v4 string is detected."""
        assert _is_uuid('5f6a7b8c-9d0e-1f2a-3b4c-5d6e7f8a9b0c') is True

    def test_valid_uuid_all_zeros(self):
        """Test that the nil UUID is detected."""
        assert _is_uuid('00000000-0000-0000-0000-000000000000') is True

    def test_human_readable_name(self):
        """Test that human-readable names are not UUIDs."""
        assert _is_uuid('Invoice Processing Agent') is False

    def test_empty_string(self):
        """Test that empty string is not a UUID."""
        assert _is_uuid('') is False

    def test_partial_uuid(self):
        """Test that a partial UUID is not detected."""
        assert _is_uuid('5f6a7b8c-9d0e') is False

    def test_uuid_uppercase(self):
        """Test that uppercase UUID is detected."""
        assert _is_uuid('5F6A7B8C-9D0E-1F2A-3B4C-5D6E7F8A9B0C') is True


class TestUuidPassthrough:
    """Test that UUID references in agent_name/knowledge_base_name are passed through."""

    def test_agent_name_with_uuid_skips_api_call(self, mock_client):
        """Scenario: UUID references are passed through.

        Given a node config has agent_name: "5f6a7b8c-9d0e-1f2a-3b4c-5d6e7f8a9b0c"
        When push resolves dependencies
        Then the UUID is used as-is (no API lookup)
        """
        agent_uuid_str = '5f6a7b8c-9d0e-1f2a-3b4c-5d6e7f8a9b0c'
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': agent_uuid_str},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        resolved = resolve_dependencies(workflow, mock_client)

        assert f'agent:{agent_uuid_str}' in resolved
        assert resolved[f'agent:{agent_uuid_str}'] == UUID(agent_uuid_str)
        # Critically: no API call should be made
        mock_client.find_agent_by_name.assert_not_called()

    def test_kb_name_with_uuid_skips_api_call(self, mock_client):
        """Scenario: UUID references are passed through for knowledge bases.

        Given a node config has knowledge_base_name: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        When push resolves dependencies
        Then the UUID is used as-is (no API lookup)
        """
        kb_uuid_str = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='KB Retrieval',
                    config={'knowledge_base_name': kb_uuid_str},
                ),
            },
            edges=[],
            entry='input',
            exit='retrieval',
        )

        resolved = resolve_dependencies(workflow, mock_client)

        assert f'kb:{kb_uuid_str}' in resolved
        assert resolved[f'kb:{kb_uuid_str}'] == UUID(kb_uuid_str)
        mock_client.find_knowledge_base_by_name.assert_not_called()

    def test_kb_names_list_with_uuids_skips_api_call(self, mock_client):
        """Test that UUID references in knowledge_base_names list are passed through."""
        kb_uuid1 = '11111111-1111-1111-1111-111111111111'
        kb_uuid2 = '22222222-2222-2222-2222-222222222222'
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    config={'knowledge_base_names': [kb_uuid1, kb_uuid2]},
                ),
            },
            edges=[],
            entry='input',
            exit='rag',
        )

        resolved = resolve_dependencies(workflow, mock_client)

        assert f'kb:{kb_uuid1}' in resolved
        assert f'kb:{kb_uuid2}' in resolved
        assert resolved[f'kb:{kb_uuid1}'] == UUID(kb_uuid1)
        assert resolved[f'kb:{kb_uuid2}'] == UUID(kb_uuid2)
        mock_client.find_knowledge_base_by_name.assert_not_called()

    def test_mixed_uuid_and_name_references(self, mock_client):
        """Test that a mix of UUID and name references works correctly."""
        kb_uuid = '11111111-1111-1111-1111-111111111111'
        kb_name = 'Company Policies'
        resolved_kb_uuid = '22222222-2222-2222-2222-222222222222'

        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    config={'knowledge_base_names': [kb_uuid, kb_name]},
                ),
            },
            edges=[],
            entry='input',
            exit='rag',
        )

        mock_client.find_knowledge_base_by_name.return_value = {
            'id': resolved_kb_uuid,
            'name': kb_name,
        }

        resolved = resolve_dependencies(workflow, mock_client)

        # UUID should be passed through
        assert resolved[f'kb:{kb_uuid}'] == UUID(kb_uuid)
        # Name should be resolved via API
        assert resolved[f'kb:{kb_name}'] == UUID(resolved_kb_uuid)
        # Only the name should trigger an API call
        mock_client.find_knowledge_base_by_name.assert_called_once_with(kb_name)


# ============================================================================
# Helpful Error Messages (RAG-950: Scenario "Missing reference shows helpful error")
# ============================================================================


class TestDependencyResolutionErrors:
    """Test that missing dependencies show helpful error messages with alternatives."""

    def test_agent_not_found_lists_available_agents(self, mock_client):
        """Scenario: Missing reference shows helpful error.

        Given a node references an agent that doesn't exist
        When push resolves dependencies
        Then the error says: "Cannot resolve agent 'X'. Available agents: A, B, C"
        And the push is aborted before any API calls
        """
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'Nonexistent Agent'},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        mock_client.find_agent_by_name.return_value = None
        mock_client.list_agents.return_value = [
            {'id': '111', 'name': 'Invoice Agent'},
            {'id': '222', 'name': 'Support Agent'},
            {'id': '333', 'name': 'Data Agent'},
        ]

        with pytest.raises(DependencyResolutionError, match='Cannot resolve agent') as exc_info:
            resolve_dependencies(workflow, mock_client)

        error_msg = str(exc_info.value)
        assert 'Nonexistent Agent' in error_msg
        assert 'Invoice Agent' in error_msg
        assert 'Support Agent' in error_msg
        assert 'Data Agent' in error_msg

    def test_kb_not_found_lists_available_kbs(self, mock_client):
        """Scenario: Missing KB reference shows helpful error with alternatives."""
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='KB Retrieval',
                    config={'knowledge_base_name': 'Nonexistent KB'},
                ),
            },
            edges=[],
            entry='input',
            exit='retrieval',
        )

        mock_client.find_knowledge_base_by_name.return_value = None
        mock_client.list_knowledge_bases.return_value = [
            {'id': '111', 'name': 'Company Policies'},
            {'id': '222', 'name': 'Product Docs'},
        ]

        with pytest.raises(
            DependencyResolutionError, match='Cannot resolve knowledge base'
        ) as exc_info:
            resolve_dependencies(workflow, mock_client)

        error_msg = str(exc_info.value)
        assert 'Nonexistent KB' in error_msg
        assert 'Company Policies' in error_msg
        assert 'Product Docs' in error_msg

    def test_agent_not_found_no_agents_available(self, mock_client):
        """Test error message when no agents exist at all."""
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'Nonexistent Agent'},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        mock_client.find_agent_by_name.return_value = None
        mock_client.list_agents.return_value = []

        with pytest.raises(DependencyResolutionError, match='Cannot resolve agent') as exc_info:
            resolve_dependencies(workflow, mock_client)

        error_msg = str(exc_info.value)
        assert 'Nonexistent Agent' in error_msg
        assert 'No agents available' in error_msg


# ============================================================================
# Lockfile Dependency Caching (RAG-950: Scenario "Resolved mappings are cached in lockfile")
# ============================================================================


class TestLockfileDependencyCache:
    """Test that resolved dependencies use lockfile cache."""

    def test_cached_agent_skips_api_call(self, mock_client):
        """Scenario: Resolved mappings are cached in lockfile.

        Given an agent name was resolved on a previous push
        When the lockfile exists with the cached UUID
        Then subsequent pushes use the cached UUID (with optional re-validation)
        """
        from cli.lockfile import WorkflowLock

        agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
        existing_lock = WorkflowLock.model_construct(
            workflow_id=UUID('99999999-9999-9999-9999-999999999999'),
            organization_id=UUID('88888888-8888-8888-8888-888888888888'),
            version=1,
            instance='https://api.example.com',
            nodes={},
            edges={},
            dependencies={'agent:Test Agent': agent_uuid},
            pushed_at=None,
        )

        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'Test Agent'},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        resolved = resolve_dependencies(workflow, mock_client, existing_lock=existing_lock)

        assert resolved['agent:Test Agent'] == agent_uuid
        # No API call should be made — used cached value
        mock_client.find_agent_by_name.assert_not_called()

    def test_cached_kb_skips_api_call(self, mock_client):
        """Test that cached KB mapping skips API call."""
        from cli.lockfile import WorkflowLock

        kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        existing_lock = WorkflowLock.model_construct(
            workflow_id=UUID('99999999-9999-9999-9999-999999999999'),
            organization_id=UUID('88888888-8888-8888-8888-888888888888'),
            version=1,
            instance='https://api.example.com',
            nodes={},
            edges={},
            dependencies={'kb:Test KB': kb_uuid},
            pushed_at=None,
        )

        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='KB Retrieval',
                    config={'knowledge_base_name': 'Test KB'},
                ),
            },
            edges=[],
            entry='input',
            exit='retrieval',
        )

        resolved = resolve_dependencies(workflow, mock_client, existing_lock=existing_lock)

        assert resolved['kb:Test KB'] == kb_uuid
        mock_client.find_knowledge_base_by_name.assert_not_called()

    def test_uncached_dependency_still_calls_api(self, mock_client):
        """Test that dependencies not in cache are resolved via API."""
        from cli.lockfile import WorkflowLock

        existing_lock = WorkflowLock.model_construct(
            workflow_id=UUID('99999999-9999-9999-9999-999999999999'),
            organization_id=UUID('88888888-8888-8888-8888-888888888888'),
            version=1,
            instance='https://api.example.com',
            nodes={},
            edges={},
            dependencies={},  # Empty cache
            pushed_at=None,
        )

        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'New Agent'},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        mock_client.find_agent_by_name.return_value = {
            'id': '12345678-1234-1234-1234-123456789012',
            'name': 'New Agent',
        }

        resolved = resolve_dependencies(workflow, mock_client, existing_lock=existing_lock)

        assert resolved['agent:New Agent'] == UUID('12345678-1234-1234-1234-123456789012')
        mock_client.find_agent_by_name.assert_called_once_with('New Agent')

    def test_no_lockfile_still_resolves_via_api(self, mock_client):
        """Test that resolve_dependencies works without a lockfile (existing_lock=None)."""
        workflow = WorkflowDefinition.model_construct(
            name='Test',
            version=1,
            nodes={
                'input': NodeDefinition(
                    type='plain_txt_input',
                    execution_mode='INPUT',
                    config={},
                ),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'Test Agent'},
                ),
            },
            edges=[],
            entry='input',
            exit='agent',
        )

        mock_client.find_agent_by_name.return_value = {
            'id': '12345678-1234-1234-1234-123456789012',
            'name': 'Test Agent',
        }

        resolved = resolve_dependencies(workflow, mock_client, existing_lock=None)

        assert resolved['agent:Test Agent'] == UUID('12345678-1234-1234-1234-123456789012')
        mock_client.find_agent_by_name.assert_called_once_with('Test Agent')
