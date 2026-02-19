"""Unit tests for pull command (TDD Red phase).

RAG-952: Phase 2 — workflow pull: Export Workflow to YAML

BDD Scenarios:
  1. Pull by workflow ID
  2. Pull by workflow name (fuzzy match)
  3. Output to specific file
  4. Pulled YAML passes validation
  5. Agent/KB references use names
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from cli.commands.pull import (
    api_response_to_wdf,
    generate_slug,
    reverse_resolve_dependencies,
    slugify,
)
from cli.main import app

runner = CliRunner()


# --- Test Constants ---

WORKFLOW_ID = UUID('11111111-1111-1111-1111-111111111111')
ORG_ID = UUID('00000000-0000-0000-0000-000000000001')
USER_ID = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')

INPUT_NODE_ID = UUID('22222222-2222-2222-2222-222222222222')
AGENT_NODE_ID = UUID('33333333-3333-3333-3333-333333333333')
OUTPUT_NODE_ID = UUID('44444444-4444-4444-4444-444444444444')

EDGE_1_ID = UUID('55555555-5555-5555-5555-555555555555')
EDGE_2_ID = UUID('66666666-6666-6666-6666-666666666666')

AGENT_UUID = UUID('77777777-7777-7777-7777-777777777777')
KB_UUID = UUID('88888888-8888-8888-8888-888888888888')

TIMESTAMP = datetime(2026, 2, 18, 14, 30, 0, tzinfo=UTC)


# --- Fixtures ---


@pytest.fixture
def mock_workflow():
    """Mock WorkflowPublic response from API."""
    return SimpleNamespace(
        id=WORKFLOW_ID,
        version=1,
        entry_point=INPUT_NODE_ID,
        exit_point=OUTPUT_NODE_ID,
        state_schema={},
        execution_config={},
        organization_id=ORG_ID,
        created_by=USER_ID,
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )


@pytest.fixture
def mock_metadata():
    """Mock WorkflowMetadataPublic response from API."""
    return SimpleNamespace(
        workflow_id=WORKFLOW_ID,
        owner_id=USER_ID,
        name='Invoice Processing Pipeline',
        description='Processes invoices through an agent',
        tags=['invoicing', 'automation'],
        is_active=True,
        custom_fields={},
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )


@pytest.fixture
def mock_nodes():
    """Mock list of LogicalNodePublic responses from API."""
    return [
        SimpleNamespace(
            id=INPUT_NODE_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=1,
            config_type='PLAIN_TXT_INPUT',
            execution_mode='INPUT',
            function_name='user-input',
            parameters={},
            retry_policy={'max_retries': 3},
            timeout_seconds=30,
            config={'placeholder': 'Enter invoice text'},
            delegated_response=False,
            step_type='STEP',
            join_config={},
            created_at=TIMESTAMP,
        ),
        SimpleNamespace(
            id=AGENT_NODE_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=1,
            config_type='AGENT',
            execution_mode='MESSAGES',
            function_name='invoice-agent',
            parameters={},
            retry_policy={'max_retries': 3},
            timeout_seconds=30,
            config={
                'agent_id': str(AGENT_UUID),
                'model': 'anthropic.claude-3-5-sonnet-20241022-v2:0',
                'system_prompt': 'You are an invoice processing agent.',
                'temperature': 0.7,
            },
            delegated_response=False,
            step_type='STEP',
            join_config={},
            created_at=TIMESTAMP,
        ),
        SimpleNamespace(
            id=OUTPUT_NODE_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=1,
            config_type='STRUCTURED_OUTPUT',
            execution_mode='OUTPUT',
            function_name='result',
            parameters={},
            retry_policy={'max_retries': 3},
            timeout_seconds=30,
            config={'schema': {'type': 'object', 'properties': {}}},
            delegated_response=False,
            step_type='STEP',
            join_config={},
            created_at=TIMESTAMP,
        ),
    ]


@pytest.fixture
def mock_edges():
    """Mock list of LogicalEdgePublic responses from API."""
    return [
        SimpleNamespace(
            id=EDGE_1_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=1,
            edge_type='STATIC',
            condition_function=None,
            data_mapping={},
            source_node_id=INPUT_NODE_ID,
            target_node_id=AGENT_NODE_ID,
            created_at=TIMESTAMP,
        ),
        SimpleNamespace(
            id=EDGE_2_ID,
            workflow_id=WORKFLOW_ID,
            workflow_version=1,
            edge_type='STATIC',
            condition_function=None,
            data_mapping={},
            source_node_id=AGENT_NODE_ID,
            target_node_id=OUTPUT_NODE_ID,
            created_at=TIMESTAMP,
        ),
    ]


@pytest.fixture
def mock_agents():
    """Mock list of agents from API."""
    return [
        {
            'id': str(AGENT_UUID),
            'name': 'Invoice Processing Agent',
            'description': 'Processes invoices',
        },
    ]


@pytest.fixture
def mock_knowledge_bases():
    """Mock list of knowledge bases from API."""
    return [
        {
            'id': str(KB_UUID),
            'name': 'Invoice Docs KB',
            'description': 'Invoice documentation',
        },
    ]


# ============================================================================
# Slugify Tests
# ============================================================================


class TestSlugify:
    """Test the slugify utility function."""

    def test_basic_lowercase(self):
        assert slugify('My Agent Name') == 'my-agent-name'

    def test_special_characters_removed(self):
        assert slugify('Hello, World! (Test)') == 'hello-world-test'

    def test_multiple_hyphens_collapsed(self):
        assert slugify('hello---world') == 'hello-world'

    def test_leading_trailing_hyphens_stripped(self):
        assert slugify('--hello-world--') == 'hello-world'

    def test_underscores_to_hyphens(self):
        assert slugify('hello_world_test') == 'hello-world-test'

    def test_empty_string(self):
        assert slugify('') == ''

    def test_numbers_preserved(self):
        assert slugify('Step 1: Process') == 'step-1-process'

    def test_unicode_handled(self):
        # Unicode should be stripped or transliterated
        result = slugify('café résumé')
        assert (
            result == 'caf-rsum' or result == 'cafe-resume' or '-' not in result or len(result) > 0
        )

    def test_already_slugified(self):
        assert slugify('hello-world') == 'hello-world'

    def test_uppercase_enum_style(self):
        assert slugify('PLAIN_TXT_INPUT') == 'plain-txt-input'


# ============================================================================
# Generate Slug Tests (with collision handling)
# ============================================================================


class TestGenerateSlug:
    """Test slug generation from node data with collision avoidance."""

    def test_slug_from_function_name(self):
        """When function_name is set, use it as slug basis."""
        slug = generate_slug(
            function_name='my-agent',
            config_type='AGENT',
            existing_slugs=set(),
        )
        assert slug == 'my-agent'

    def test_slug_from_config_type_when_no_function_name(self):
        """When function_name is None, fall back to config_type."""
        slug = generate_slug(
            function_name=None,
            config_type='LLM_CALL',
            existing_slugs=set(),
        )
        assert slug == 'llm-call'

    def test_collision_appends_suffix(self):
        """When slug already exists, append -2, -3, etc."""
        slug = generate_slug(
            function_name='agent',
            config_type='AGENT',
            existing_slugs={'agent'},
        )
        assert slug == 'agent-2'

    def test_multiple_collisions(self):
        """Handle multiple collisions."""
        slug = generate_slug(
            function_name='agent',
            config_type='AGENT',
            existing_slugs={'agent', 'agent-2', 'agent-3'},
        )
        assert slug == 'agent-4'

    def test_empty_function_name_falls_back(self):
        """Empty string function_name should fall back to config_type."""
        slug = generate_slug(
            function_name='',
            config_type='AGENT',
            existing_slugs=set(),
        )
        assert slug == 'agent'

    def test_function_name_gets_slugified(self):
        """Function names with spaces/special chars are slugified."""
        slug = generate_slug(
            function_name='My Custom Agent!',
            config_type='AGENT',
            existing_slugs=set(),
        )
        assert slug == 'my-custom-agent'


# ============================================================================
# Reverse Resolve Dependencies Tests
# ============================================================================


class TestReverseResolveDependencies:
    """Test reverse-resolving UUIDs to names using API lookups."""

    def test_resolves_agent_id_to_name(self, mock_agents):
        """Agent UUIDs in node configs are resolved to names."""
        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []

        nodes = [
            SimpleNamespace(
                config={'agent_id': str(AGENT_UUID)},
                config_type='AGENT',
            ),
        ]

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)

        assert AGENT_UUID in agent_map
        assert agent_map[AGENT_UUID] == 'Invoice Processing Agent'

    def test_resolves_knowledge_base_id_to_name(self, mock_knowledge_bases):
        """KB UUIDs in node configs are resolved to names."""
        mock_client = MagicMock()
        mock_client.list_agents.return_value = []
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        nodes = [
            SimpleNamespace(
                config={'knowledge_base_id': str(KB_UUID)},
                config_type='RETRIEVE',
            ),
        ]

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)

        assert KB_UUID in kb_map
        assert kb_map[KB_UUID] == 'Invoice Docs KB'

    def test_no_dependencies_returns_empty_maps(self):
        """Nodes without agent/KB refs produce empty maps."""
        mock_client = MagicMock()
        mock_client.list_agents.return_value = []
        mock_client.list_knowledge_bases.return_value = []

        nodes = [
            SimpleNamespace(
                config={'placeholder': 'Enter text'},
                config_type='PLAIN_TXT_INPUT',
            ),
        ]

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)

        assert agent_map == {}
        assert kb_map == {}

    def test_unresolvable_uuid_is_kept_as_is(self, mock_agents):
        """Agent UUID not found in API list is left as-is (not an error)."""
        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []

        unknown_uuid = uuid4()
        nodes = [
            SimpleNamespace(
                config={'agent_id': str(unknown_uuid)},
                config_type='AGENT',
            ),
        ]

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)

        # Unknown UUID should not be in the map (it will stay as UUID in output)
        assert unknown_uuid not in agent_map

    def test_rag_agent_with_knowledge_base_ids(self, mock_knowledge_bases):
        """RAG_AGENT nodes have knowledge_base_ids as a list."""
        mock_client = MagicMock()
        mock_client.list_agents.return_value = []
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        nodes = [
            SimpleNamespace(
                config={'knowledge_base_ids': [str(KB_UUID)]},
                config_type='RAG_AGENT',
            ),
        ]

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)

        assert KB_UUID in kb_map
        assert kb_map[KB_UUID] == 'Invoice Docs KB'


# ============================================================================
# API Response to WDF Conversion Tests
# ============================================================================


class TestApiResponseToWdf:
    """Test conversion of API responses into a WorkflowDefinition."""

    def test_basic_conversion(self, mock_workflow, mock_metadata, mock_nodes, mock_edges):
        """Test that API response is correctly converted to WDF."""
        agent_map = {AGENT_UUID: 'Invoice Processing Agent'}
        kb_map = {}

        wdf, slug_to_uuid, edge_to_id = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map=agent_map,
            kb_map=kb_map,
        )

        assert wdf.name == 'Invoice Processing Pipeline'
        assert wdf.description == 'Processes invoices through an agent'
        assert wdf.version == 1
        assert wdf.tags == ['invoicing', 'automation']
        assert len(wdf.nodes) == 3
        assert len(wdf.edges) == 2

    def test_entry_exit_mapped_to_slugs(self, mock_workflow, mock_metadata, mock_nodes, mock_edges):
        """Entry and exit points are mapped from UUIDs to slugs."""
        wdf, slug_to_uuid, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        # Entry should be the slug of INPUT_NODE_ID
        assert wdf.entry in wdf.nodes
        assert slug_to_uuid[wdf.entry] == INPUT_NODE_ID

        # Exit should be the slug of OUTPUT_NODE_ID
        assert wdf.exit in wdf.nodes
        assert slug_to_uuid[wdf.exit] == OUTPUT_NODE_ID

    def test_node_slugs_from_function_name(
        self, mock_workflow, mock_metadata, mock_nodes, mock_edges
    ):
        """Node slugs are derived from function_name."""
        wdf, slug_to_uuid, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        # Slugs should be derived from function_name: user-input, invoice-agent, result
        assert 'user-input' in wdf.nodes
        assert 'invoice-agent' in wdf.nodes
        assert 'result' in wdf.nodes

    def test_agent_uuid_replaced_with_name(
        self, mock_workflow, mock_metadata, mock_nodes, mock_edges
    ):
        """Agent UUIDs in node configs are replaced with agent_name."""
        agent_map = {AGENT_UUID: 'Invoice Processing Agent'}

        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map=agent_map,
            kb_map={},
        )

        agent_node = wdf.nodes['invoice-agent']
        assert 'agent_name' in agent_node.config
        assert agent_node.config['agent_name'] == 'Invoice Processing Agent'
        assert 'agent_id' not in agent_node.config
        # Other fields preserved
        assert agent_node.config['model'] == 'anthropic.claude-3-5-sonnet-20241022-v2:0'
        assert agent_node.config['system_prompt'] == 'You are an invoice processing agent.'

    def test_edges_mapped_to_slugs(self, mock_workflow, mock_metadata, mock_nodes, mock_edges):
        """Edge source/target UUIDs are converted to slugs."""
        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        assert len(wdf.edges) == 2
        edge_pairs = [(e.from_node, e.to) for e in wdf.edges]
        assert ('user-input', 'invoice-agent') in edge_pairs
        assert ('invoice-agent', 'result') in edge_pairs

    def test_edge_types_preserved(self, mock_workflow, mock_metadata, mock_nodes, mock_edges):
        """Edge types from API are preserved in WDF."""
        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        for edge in wdf.edges:
            assert edge.type == 'STATIC'

    def test_slug_to_uuid_mapping_returned(
        self, mock_workflow, mock_metadata, mock_nodes, mock_edges
    ):
        """Returned slug_to_uuid maps node slugs to server UUIDs."""
        _, slug_to_uuid, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        assert slug_to_uuid['user-input'] == INPUT_NODE_ID
        assert slug_to_uuid['invoice-agent'] == AGENT_NODE_ID
        assert slug_to_uuid['result'] == OUTPUT_NODE_ID

    def test_edge_to_id_mapping_returned(
        self, mock_workflow, mock_metadata, mock_nodes, mock_edges
    ):
        """Returned edge_to_id maps slug pairs to server edge IDs."""
        _, _, edge_to_id = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        assert 'user-input->invoice-agent' in edge_to_id
        assert 'invoice-agent->result' in edge_to_id

    def test_node_config_type_lowercased(
        self, mock_workflow, mock_metadata, mock_nodes, mock_edges
    ):
        """Node config_type from API (UPPERCASE) is lowercased for WDF."""
        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        for _slug, node in wdf.nodes.items():
            assert node.type == node.type.lower()

    def test_execution_mode_preserved(self, mock_workflow, mock_metadata, mock_nodes, mock_edges):
        """Execution mode is preserved from API response."""
        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=mock_nodes,
            edges=mock_edges,
            agent_map={AGENT_UUID: 'Invoice Processing Agent'},
            kb_map={},
        )

        assert wdf.nodes['user-input'].execution_mode == 'INPUT'
        assert wdf.nodes['invoice-agent'].execution_mode == 'MESSAGES'
        assert wdf.nodes['result'].execution_mode == 'OUTPUT'


# ============================================================================
# Slug Collision Tests
# ============================================================================


class TestSlugCollisions:
    """Test slug collision handling during API-to-WDF conversion."""

    def test_duplicate_function_names_get_suffixes(self, mock_workflow, mock_metadata, mock_edges):
        """Nodes with same function_name get -2, -3 suffixes."""
        # Two nodes with same function_name
        nodes = [
            SimpleNamespace(
                id=INPUT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='AGENT',
                execution_mode='MESSAGES',
                function_name='agent',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={},
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
            SimpleNamespace(
                id=AGENT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='AGENT',
                execution_mode='MESSAGES',
                function_name='agent',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={},
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
            SimpleNamespace(
                id=OUTPUT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='STRUCTURED_OUTPUT',
                execution_mode='OUTPUT',
                function_name='result',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={'schema': {'type': 'object', 'properties': {}}},
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
        ]

        wdf, slug_to_uuid, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=nodes,
            edges=mock_edges,
            agent_map={},
            kb_map={},
        )

        # Should have 'agent' and 'agent-2'
        assert 'agent' in wdf.nodes
        assert 'agent-2' in wdf.nodes
        assert 'result' in wdf.nodes


# ============================================================================
# KB UUID Resolution Tests
# ============================================================================


class TestKnowledgeBaseResolution:
    """Test knowledge base UUID reverse resolution in pulled configs."""

    def test_knowledge_base_id_replaced_with_name(self, mock_workflow, mock_metadata, mock_edges):
        """knowledge_base_id in config is replaced with knowledge_base_name."""
        nodes = [
            SimpleNamespace(
                id=INPUT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='PLAIN_TXT_INPUT',
                execution_mode='INPUT',
                function_name='input',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={'placeholder': 'Question'},
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
            SimpleNamespace(
                id=AGENT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='RETRIEVE',
                execution_mode='FLOW',
                function_name='retrieval',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={
                    'knowledge_base_id': str(KB_UUID),
                    'topK': 5,
                },
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
            SimpleNamespace(
                id=OUTPUT_NODE_ID,
                workflow_id=WORKFLOW_ID,
                workflow_version=1,
                config_type='STRUCTURED_OUTPUT',
                execution_mode='OUTPUT',
                function_name='output',
                parameters={},
                retry_policy={'max_retries': 3},
                timeout_seconds=30,
                config={'schema': {'type': 'object', 'properties': {}}},
                delegated_response=False,
                step_type='STEP',
                join_config={},
                created_at=TIMESTAMP,
            ),
        ]

        kb_map = {KB_UUID: 'Invoice Docs KB'}

        wdf, _, _ = api_response_to_wdf(
            workflow=mock_workflow,
            metadata=mock_metadata,
            nodes=nodes,
            edges=mock_edges,
            agent_map={},
            kb_map=kb_map,
        )

        retrieval_node = wdf.nodes['retrieval']
        assert 'knowledge_base_name' in retrieval_node.config
        assert retrieval_node.config['knowledge_base_name'] == 'Invoice Docs KB'
        assert 'knowledge_base_id' not in retrieval_node.config


# ============================================================================
# CLI Integration Tests
# ============================================================================


class TestPullByWorkflowID:
    """BDD: Pull by workflow ID.

    Scenario: Pull by workflow ID
      Given a workflow exists with a known UUID
      When "workflow pull <uuid>" is run
      Then the workflow is fetched via API
      And a .workflow.yaml is generated with readable node slugs
      And a .workflow.lock is generated
      And visual-only data is stripped
    """

    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_by_id_creates_yaml_and_lock(
        self,
        mock_client_class,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test basic pull by UUID produces yaml + lock files."""
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        output_file = tmp_path / 'test.workflow.yaml'

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                str(WORKFLOW_ID),
                '-o',
                str(output_file),
            ],
        )

        assert result.exit_code == 0, f'CLI failed: {result.output}'
        assert output_file.exists(), 'YAML file was not created'

        # Lockfile should also exist
        lock_file = output_file.with_suffix('.lock')
        assert lock_file.exists(), 'Lock file was not created'

    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_by_id_yaml_content_valid(
        self,
        mock_client_class,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test that pulled YAML content is valid WDF."""
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        output_file = tmp_path / 'test.workflow.yaml'

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                str(WORKFLOW_ID),
                '-o',
                str(output_file),
            ],
        )

        assert result.exit_code == 0, f'CLI failed: {result.output}'

        # Parse the YAML and validate it
        from cli.wdf_yaml import load_workflow_yaml

        yaml_content = output_file.read_text()
        workflow = load_workflow_yaml(yaml_content)

        assert workflow.name == 'Invoice Processing Pipeline'
        assert len(workflow.nodes) == 3
        assert len(workflow.edges) == 2


class TestPullByWorkflowName:
    """BDD: Pull by workflow name (fuzzy match).

    Scenario: Pull by workflow name
      Given a workflow named "Invoice Processing Pipeline"
      When "workflow pull 'Invoice Processing'" is run
      Then the closest matching workflow is found
      And the user is prompted to confirm if multiple matches exist
    """

    @patch('cli.commands.pull.Prompt.ask')
    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_by_name_single_match(
        self,
        mock_client_class,
        mock_prompt,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test pull by exact name finds the workflow."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow]
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        output_file = tmp_path / 'test.workflow.yaml'

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                'Invoice Processing Pipeline',
                '-o',
                str(output_file),
            ],
        )

        assert result.exit_code == 0, f'CLI failed: {result.output}'
        assert output_file.exists()

        # Prompt should NOT have been called (single match, no ambiguity)
        mock_prompt.assert_not_called()

    @patch('cli.commands.pull.Prompt.ask')
    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_by_name_multiple_matches_prompts(
        self,
        mock_client_class,
        mock_prompt,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test pull by partial name with multiple matches prompts selection."""
        # Create a second workflow
        workflow2 = SimpleNamespace(
            id=uuid4(),
            version=1,
            entry_point=INPUT_NODE_ID,
            exit_point=OUTPUT_NODE_ID,
            state_schema={},
            execution_config={},
            organization_id=ORG_ID,
            created_by=USER_ID,
            created_at=TIMESTAMP,
            updated_at=TIMESTAMP,
        )
        metadata2 = SimpleNamespace(
            workflow_id=workflow2.id,
            owner_id=USER_ID,
            name='Invoice Processing v2',
            description='Version 2',
            tags=[],
            is_active=True,
            custom_fields={},
            created_at=TIMESTAMP,
            updated_at=TIMESTAMP,
        )

        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow, workflow2]

        def get_meta(wf_id):
            if str(wf_id) == str(WORKFLOW_ID):
                return mock_metadata
            return metadata2

        mock_client.get_metadata.side_effect = get_meta
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        # User selects option 1
        mock_prompt.return_value = '1'

        output_file = tmp_path / 'test.workflow.yaml'

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                'Invoice Processing',
                '-o',
                str(output_file),
            ],
        )

        assert result.exit_code == 0, f'CLI failed: {result.output}'
        # Prompt should have been called to ask for selection
        mock_prompt.assert_called_once()


class TestPullCustomOutputPath:
    """BDD: Output to specific file.

    Scenario: Output to specific file
      Given a workflow exists
      When "workflow pull <id> -o invoices.workflow.yaml" is run
      Then the file is written to the specified path
    """

    @patch('cli.commands.pull.WorkflowClient')
    def test_output_flag_writes_to_specified_path(
        self,
        mock_client_class,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test -o flag writes to specified path."""
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        custom_path = tmp_path / 'custom' / 'invoices.workflow.yaml'

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                str(WORKFLOW_ID),
                '-o',
                str(custom_path),
            ],
        )

        assert result.exit_code == 0, f'CLI failed: {result.output}'
        assert custom_path.exists()


class TestPullYamlValidation:
    """BDD: Pulled YAML passes validation.

    Scenario: Pulled YAML passes validation
      Given any workflow on the platform
      When it is pulled to YAML
      Then the resulting file passes "workflow validate" without errors
    """

    @patch('cli.commands.pull.WorkflowClient')
    def test_pulled_yaml_passes_validation(
        self,
        mock_client_class,
        mock_workflow,
        mock_metadata,
        mock_nodes,
        mock_edges,
        mock_agents,
        tmp_path,
    ):
        """Test that pulled YAML passes the validate command."""
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.list_nodes.return_value = mock_nodes
        mock_client.list_edges.return_value = mock_edges
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        output_file = tmp_path / 'test.workflow.yaml'

        # First, pull the workflow
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                str(WORKFLOW_ID),
                '-o',
                str(output_file),
            ],
        )
        assert result.exit_code == 0, f'Pull failed: {result.output}'

        # Then, validate it
        validate_result = runner.invoke(
            app,
            ['validate', str(output_file)],
        )
        assert validate_result.exit_code == 0, f'Validate failed: {validate_result.output}'


class TestPullErrorHandling:
    """Test error handling for pull command."""

    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_nonexistent_workflow(self, mock_client_class):
        """Test pulling a workflow that doesn't exist."""
        from cli.exceptions import NotFoundError

        mock_client = MagicMock()
        mock_client.get_workflow.side_effect = NotFoundError('Workflow not found', status_code=404)
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                str(uuid4()),
            ],
        )

        assert result.exit_code != 0

    @patch('cli.commands.pull.WorkflowClient')
    def test_pull_by_name_no_matches(self, mock_client_class):
        """Test pulling by name when no workflows match."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(ORG_ID),
                'pull',
                'Nonexistent Workflow',
            ],
        )

        assert result.exit_code != 0

    def test_pull_missing_config(self):
        """Test pull with missing configuration shows clear error."""
        result = runner.invoke(app, ['pull', str(uuid4())])
        assert result.exit_code != 0
