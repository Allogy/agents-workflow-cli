"""Unit tests for push command (TDD Red phase)."""

from __future__ import annotations

from uuid import UUID

import pytest
from workflow_models.wdf import EdgeDefinition, NodeDefinition, WorkflowDefinition

from cli.commands.push import (
    DependencyResolutionError,
    PushError,
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

    with pytest.raises(DependencyResolutionError, match='Agent not found: Test Agent'):
        resolve_dependencies(workflow_with_agent, mock_client)


def test_resolve_dependencies_kb_not_found(workflow_with_kb, mock_client):
    """Test error when knowledge base cannot be resolved."""
    mock_client.find_knowledge_base_by_name.return_value = None

    with pytest.raises(DependencyResolutionError, match='Knowledge base not found: Test KB'):
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

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert 'workflow' in payload
    assert payload['workflow']['name'] == 'Test Workflow'
    assert payload['workflow']['description'] == 'A simple test workflow'
    assert payload['workflow']['version'] == 1
    assert payload['workflow']['organization_id'] == str(org_id)
    assert 'id' not in payload['workflow']  # Create mode

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

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id, workflow_id)

    assert payload['workflow']['id'] == str(workflow_id)


def test_wdf_to_api_payload_includes_nodes(simple_workflow):
    """Test that payload includes all nodes."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['nodes']) == 3
    # Check that nodes have required fields
    for node in payload['nodes']:
        assert 'slug' in node
        assert 'node_config_type' in node
        assert 'execution_mode' in node
        assert 'label' in node
        assert 'config' in node


def test_wdf_to_api_payload_includes_edges(simple_workflow):
    """Test that payload includes all edges."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['edges']) == 2
    # Check that edges have required fields
    for edge in payload['edges']:
        assert 'source_node_slug' in edge
        assert 'target_node_slug' in edge
        assert 'edge_type' in edge


def test_wdf_to_api_payload_includes_visuals(simple_workflow):
    """Test that payload includes node visuals with positions."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert len(payload['node_visuals']) == 3
    # Check that visuals have positions
    for visual in payload['node_visuals']:
        assert 'node_slug' in visual
        assert 'position_x' in visual
        assert 'position_y' in visual


def test_wdf_to_api_payload_resolves_agent_refs(workflow_with_agent):
    """Test that agent references are resolved to UUIDs."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(workflow_with_agent)
    agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
    resolved_deps = {'agent:Test Agent': agent_uuid}

    payload = wdf_to_api_payload(workflow_with_agent, resolved_deps, layout, org_id)

    # Find the agent node
    agent_node = next(n for n in payload['nodes'] if n['slug'] == 'agent')
    assert agent_node['config']['agent_id'] == str(agent_uuid)
    assert 'agent_name' not in agent_node['config']  # Name should be replaced


def test_wdf_to_api_payload_resolves_kb_refs(workflow_with_kb):
    """Test that knowledge base references are resolved to UUIDs."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(workflow_with_kb)
    kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    resolved_deps = {'kb:Test KB': kb_uuid}

    payload = wdf_to_api_payload(workflow_with_kb, resolved_deps, layout, org_id)

    # Find the retrieval node
    retrieval_node = next(n for n in payload['nodes'] if n['slug'] == 'retrieval')
    assert retrieval_node['config']['knowledge_base_id'] == str(kb_uuid)
    assert 'knowledge_base_name' not in retrieval_node['config']  # Name should be replaced


def test_wdf_to_api_payload_generates_io_for_nodes(simple_workflow):
    """Test that node inputs and outputs are generated."""
    org_id = UUID('99999999-9999-9999-9999-999999999999')
    layout = generate_node_layout(simple_workflow)
    resolved_deps = {}

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

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

    payload = wdf_to_api_payload(simple_workflow, resolved_deps, layout, org_id)

    assert payload['workflow']['description'] == ''


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
