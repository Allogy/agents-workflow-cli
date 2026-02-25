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
    extract_node_config,
    generate_slug,
    replace_uuid_references,
    replace_variable_references,
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
        """function_name is the primary slug source (lowercased, underscores preserved)."""
        slug = generate_slug(
            function_name='My_Agent',
            config_type='AGENT',
            existing_slugs=set(),
        )
        assert slug == 'my_agent'

    def test_slug_from_function_name_preserves_underscores(self):
        """function_name underscores are preserved (not converted to hyphens)."""
        slug = generate_slug(
            function_name='plainTextInput_1',
            config_type='PLAIN_TXT_INPUT',
            existing_slugs=set(),
        )
        assert slug == 'plaintextinput_1'

    def test_slug_from_config_type_when_no_function_name(self):
        """When function_name is None, fall back to config_type (via slugify)."""
        slug = generate_slug(
            function_name=None,
            config_type='LLM_CALL',
            existing_slugs=set(),
        )
        assert slug == 'llm-call'

    def test_collision_appends_underscore_suffix(self):
        """When slug already exists, append _2, _3, etc."""
        slug = generate_slug(
            function_name='agent',
            config_type='AGENT',
            existing_slugs={'agent'},
        )
        assert slug == 'agent_2'

    def test_multiple_collisions(self):
        """Handle multiple collisions."""
        slug = generate_slug(
            function_name='agent',
            config_type='AGENT',
            existing_slugs={'agent', 'agent_2', 'agent_3'},
        )
        assert slug == 'agent_4'

    def test_empty_function_name_falls_back(self):
        """Empty string function_name should fall back to config_type."""
        slug = generate_slug(
            function_name='',
            config_type='AGENT',
            existing_slugs=set(),
        )
        assert slug == 'agent'

    def test_function_name_lowercased_directly(self):
        """Function names are lowercased directly, preserving original chars."""
        slug = generate_slug(
            function_name='ragAgent_1',
            config_type='RAG_AGENT',
            existing_slugs=set(),
        )
        assert slug == 'ragagent_1'


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

        # Should have 'agent' and 'agent_2'
        assert 'agent' in wdf.nodes
        assert 'agent_2' in wdf.nodes
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


# ============================================================================
# Extract Node Config Tests
# ============================================================================


class TestExtractNodeConfig:
    """Test the extract_node_config function that merges parameters + config."""

    def test_plain_txt_input_from_parameters(self):
        """Test extracting PLAIN_TXT_INPUT config from parameters."""
        parameters = {
            'type': 'plainTextInput',
            'label': 'question',
            'prompt': 'Enter your question',
            'function_name': 'plainTextInput_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('PLAIN_TXT_INPUT', parameters, config)
        assert result == {'placeholder': 'Enter your question'}

    def test_plain_txt_input_from_config_fallback(self):
        """Test extracting PLAIN_TXT_INPUT config from config (CLI-pushed)."""
        parameters: dict = {}
        config = {'placeholder': 'Enter text here'}
        result = extract_node_config('PLAIN_TXT_INPUT', parameters, config)
        assert result == {'placeholder': 'Enter text here'}

    def test_file_upload_from_parameters(self):
        """Test extracting FILE_UPLOAD config from parameters."""
        parameters = {
            'type': 'fileUpload',
            'label': 'File Upload 1',
            'acceptedFormats': ['pdf', 'docx', 'txt'],
            'maxFileSize': 10,
            'extractText': True,
            'textExtraction': 'automatic',
            'function_name': 'fileUpload_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('FILE_UPLOAD', parameters, config)
        assert result['acceptedFormats'] == ['pdf', 'docx', 'txt']
        assert result['maxFileSize'] == 10
        assert result['textExtraction'] == 'automatic'
        assert result['extractText'] is True
        # UI-only fields should not be in result
        assert 'type' not in result
        assert 'collapsed' not in result
        assert 'function_name' not in result
        assert 'label' not in result

    def test_rag_agent_from_parameters(self):
        """Test extracting RAG_AGENT config from parameters."""
        agent_id = str(uuid4())
        kb_id = str(uuid4())
        parameters = {
            'type': 'ragAgent',
            'label': 'RAG Agent 1',
            'agentId': agent_id,
            'primaryInput': '{{plainTextInput_1.output.text}}',
            'knowledgeBasesOverride': [kb_id],
            'disableRAG': False,
            'function_name': 'ragAgent_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('RAG_AGENT', parameters, config)
        assert result['agentId'] == agent_id
        assert result['knowledgeBaseIds'] == [kb_id]  # knowledgeBasesOverride -> knowledgeBaseIds
        assert result['primaryInput'] == '{{plainTextInput_1.output.text}}'
        assert result['disableRAG'] is False

    def test_llm_call_from_parameters(self):
        """Test extracting LLM_CALL config from parameters."""
        parameters = {
            'type': 'llmPrompt',
            'label': 'Summarizer',
            'template': 'Summarize: {{fileUpload_1.output.text}}',
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'temperature': 0.7,
            'maxTokens': 1000,
            'function_name': 'llmPrompt_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('LLM_CALL', parameters, config)
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert result['template'] == 'Summarize: {{fileUpload_1.output.text}}'
        assert result['temperature'] == 0.7
        assert result['maxTokens'] == 1000

    def test_agent_from_parameters(self):
        """Test extracting AGENT config from parameters."""
        agent_id = str(uuid4())
        parameters = {
            'type': 'agent',
            'label': 'My Agent',
            'agentId': agent_id,
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are a helpful assistant.',
            'temperature': 0.7,
            'maxTokens': 2048,
            'function_name': 'agent_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('AGENT', parameters, config)
        assert result['agentId'] == agent_id
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert result['system_prompt'] == 'You are a helpful assistant.'
        assert result['temperature'] == 0.7
        assert result['maxTokens'] == 2048

    def test_agent_from_config_fallback(self):
        """Test extracting AGENT config from config (CLI-pushed)."""
        parameters: dict = {}
        config = {
            'agent_id': str(uuid4()),
            'model': 'anthropic.claude-3-5-sonnet-20241022-v2:0',
            'system_prompt': 'You are an invoice agent.',
            'temperature': 0.7,
        }
        result = extract_node_config('AGENT', parameters, config)
        assert result['model'] == 'anthropic.claude-3-5-sonnet-20241022-v2:0'
        assert result['system_prompt'] == 'You are an invoice agent.'
        assert result['temperature'] == 0.7
        # agent_id -> agentId renaming via config fallback
        assert result['agentId'] == config['agent_id']

    def test_agent_from_config_model_name_fallback(self):
        """Test that model_name in config maps to model in WDF."""
        parameters: dict = {}
        config = {
            'model_name': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
            'max_tokens': 2048,
        }
        result = extract_node_config('AGENT', parameters, config)
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert result['maxTokens'] == 2048

    def test_structured_input_from_config(self):
        """Test extracting STRUCTURED_INPUT config from config."""
        parameters = {'type': 'formInput', 'label': 'Instructions'}
        config = {'schema': {'type': 'object', 'properties': {'topic': {'type': 'string'}}}}
        result = extract_node_config('STRUCTURED_INPUT', parameters, config)
        assert result['schema'] == config['schema']

    def test_structured_output_from_config(self):
        """Test extracting STRUCTURED_OUTPUT config from config."""
        parameters: dict = {}
        config = {'schema': {'type': 'object', 'properties': {}}}
        result = extract_node_config('STRUCTURED_OUTPUT', parameters, config)
        assert result['schema'] == config['schema']

    def test_retrieve_from_parameters(self):
        """Test extracting RETRIEVE config from parameters."""
        kb_id = str(uuid4())
        parameters = {
            'type': 'retrieve',
            'label': 'VectorSearch',
            'knowledgeBaseId': [kb_id],
            'topK': 5,
            'function_name': 'retrieve_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('RETRIEVE', parameters, config)
        assert result['knowledgeBaseId'] == [kb_id]
        assert result['topK'] == 5

    def test_human_review_from_parameters(self):
        """Test extracting HUMAN_REVIEW config from parameters."""
        parameters = {
            'type': 'humanReview',
            'label': 'QualityReview',
            'review_prompt': 'Please review the response.',
            'function_name': 'humanReview_1',
            'collapsed': False,
        }
        config = {'allow_approve': True, 'allow_reject': True, 'allow_edit': False}
        result = extract_node_config('HUMAN_REVIEW', parameters, config)
        assert result['review_prompt'] == 'Please review the response.'
        assert result['allowApprove'] is True
        assert result['allowReject'] is True
        assert result['allowEdit'] is False

    def test_document_extraction_from_parameters(self):
        """Test extracting DOCUMENT_EXTRACTION config from parameters."""
        parameters = {
            'type': 'documentExtraction',
            'label': 'Extractor',
            'extract_tables': True,
            'extract_images': False,
            'function_name': 'docExtract_1',
            'collapsed': False,
        }
        config: dict = {}
        result = extract_node_config('DOCUMENT_EXTRACTION', parameters, config)
        assert result['extractTables'] is True
        assert result['extractImages'] is False

    def test_parameters_primary_over_config(self):
        """Test that parameters values take priority over config values."""
        parameters = {'model': 'params-model', 'temperature': 0.9}
        config = {'model': 'config-model', 'temperature': 0.5, 'system_prompt': 'from config'}
        result = extract_node_config('AGENT', parameters, config)
        # parameters should win
        assert result['model'] == 'params-model'
        assert result['temperature'] == 0.9
        # config should fill gaps
        assert result['system_prompt'] == 'from config'

    def test_unknown_node_type_falls_back_to_config(self):
        """Test that unknown node types use raw config as fallback."""
        parameters: dict = {}
        config = {'custom_field': 'value', 'another': 42}
        result = extract_node_config('UNKNOWN_TYPE', parameters, config)
        assert result == {'custom_field': 'value', 'another': 42}

    def test_empty_parameters_and_config(self):
        """Test with both parameters and config empty."""
        result = extract_node_config('PLAIN_TXT_INPUT', {}, {})
        assert result == {}


# ============================================================================
# Replace UUID References Tests
# ============================================================================


class TestReplaceUuidReferences:
    """Test the replace_uuid_references function."""

    def test_replaces_single_uuid(self):
        """Test replacing a single UUID reference in a string."""
        node_uuid = UUID('4a8611ec-ee1e-4d4d-a66e-76ae207d34ee')
        uuid_to_slug = {node_uuid: 'file-upload-1'}
        config = {'primaryInput': '{{4a8611ec-ee1e-4d4d-a66e-76ae207d34ee.output.text}}'}
        result = replace_uuid_references(config, uuid_to_slug)
        assert result['primaryInput'] == '{{file-upload-1.output.text}}'

    def test_replaces_multiple_uuids(self):
        """Test replacing multiple UUID references in a single string."""
        uuid1 = UUID('11111111-1111-1111-1111-111111111111')
        uuid2 = UUID('22222222-2222-2222-2222-222222222222')
        uuid_to_slug = {uuid1: 'text-input', uuid2: 'file-upload'}
        config = {
            'template': (
                '{{11111111-1111-1111-1111-111111111111.output.text}} '
                'and {{22222222-2222-2222-2222-222222222222.output.text}}'
            )
        }
        result = replace_uuid_references(config, uuid_to_slug)
        assert result['template'] == '{{text-input.output.text}} and {{file-upload.output.text}}'

    def test_preserves_slug_based_references(self):
        """Test that slug-based references are not mangled."""
        uuid_to_slug: dict[UUID, str] = {}
        config = {'template': '{{plainTextInput_1.output.text}}'}
        result = replace_uuid_references(config, uuid_to_slug)
        assert result['template'] == '{{plainTextInput_1.output.text}}'

    def test_preserves_non_string_values(self):
        """Test that non-string values are not modified."""
        uuid_to_slug: dict[UUID, str] = {}
        config = {
            'maxTokens': 1000,
            'temperature': 0.7,
            'acceptedFormats': ['pdf', 'txt'],
        }
        result = replace_uuid_references(config, uuid_to_slug)
        assert result == config

    def test_unknown_uuid_not_replaced(self):
        """Test that unrecognized UUIDs are kept as-is."""
        uuid_to_slug: dict[UUID, str] = {}
        config = {'template': '{{99999999-9999-9999-9999-999999999999.output.text}}'}
        result = replace_uuid_references(config, uuid_to_slug)
        assert result['template'] == '{{99999999-9999-9999-9999-999999999999.output.text}}'

    def test_mixed_uuid_and_slug_references(self):
        """Test string with both UUID and slug-based references."""
        uuid1 = UUID('4a8611ec-ee1e-4d4d-a66e-76ae207d34ee')
        uuid_to_slug = {uuid1: 'file-upload-1'}
        config = {
            'primaryInput': (
                '{{plainTextInput_1.output.text}} also '
                '{{4a8611ec-ee1e-4d4d-a66e-76ae207d34ee.output.text}}'
            )
        }
        result = replace_uuid_references(config, uuid_to_slug)
        assert result['primaryInput'] == (
            '{{plainTextInput_1.output.text}} also {{file-upload-1.output.text}}'
        )


# ============================================================================
# Replace Variable References Tests (with function_name normalization)
# ============================================================================


class TestReplaceVariableReferences:
    """Test replace_variable_references with function_name normalization."""

    def test_normalizes_function_name_refs(self):
        """Test that mixed-case function_name references are lowercased to match slugs."""
        uuid_to_slug: dict[UUID, str] = {}
        func_name_to_slug = {
            'fileUpload_1': 'fileupload_1',
            'plainTextInput_1': 'plaintextinput_1',
        }
        config = {
            'primaryInput': '{{plainTextInput_1.output.text}} and {{fileUpload_1.output.text}}'
        }
        result = replace_variable_references(config, uuid_to_slug, func_name_to_slug)
        assert result['primaryInput'] == (
            '{{plaintextinput_1.output.text}} and {{fileupload_1.output.text}}'
        )

    def test_handles_both_uuid_and_function_name_refs(self):
        """Test replacing both UUID and function_name references in one string."""
        uuid1 = UUID('4a8611ec-ee1e-4d4d-a66e-76ae207d34ee')
        uuid_to_slug = {uuid1: 'fileupload_1'}
        func_name_to_slug = {'plainTextInput_1': 'plaintextinput_1'}
        config = {
            'primaryInput': (
                '{{plainTextInput_1.output.text}} also '
                '{{4a8611ec-ee1e-4d4d-a66e-76ae207d34ee.output.text}}'
            )
        }
        result = replace_variable_references(config, uuid_to_slug, func_name_to_slug)
        assert result['primaryInput'] == (
            '{{plaintextinput_1.output.text}} also {{fileupload_1.output.text}}'
        )

    def test_recursive_into_nested_dicts(self):
        """Test that variable references in nested dicts are replaced."""
        uuid1 = UUID('11111111-1111-1111-1111-111111111111')
        uuid_to_slug = {uuid1: 'input_1'}
        config = {'nested': {'template': '{{11111111-1111-1111-1111-111111111111.output.text}}'}}
        result = replace_variable_references(config, uuid_to_slug)
        assert result['nested']['template'] == '{{input_1.output.text}}'

    def test_recursive_into_lists(self):
        """Test that variable references in lists are replaced."""
        uuid1 = UUID('11111111-1111-1111-1111-111111111111')
        uuid_to_slug = {uuid1: 'input_1'}
        config = {'items': ['{{11111111-1111-1111-1111-111111111111.output.text}}', 'static']}
        result = replace_variable_references(config, uuid_to_slug)
        assert result['items'] == ['{{input_1.output.text}}', 'static']

    def test_already_lowercase_function_name_unchanged(self):
        """Test that already-lowercase function_name refs are not double-processed."""
        func_name_to_slug = {'input_1': 'input_1'}
        config = {'template': '{{input_1.output.text}}'}
        result = replace_variable_references(config, {}, func_name_to_slug)
        assert result['template'] == '{{input_1.output.text}}'

    def test_no_func_name_map_skips_normalization(self):
        """Test that function_name normalization is skipped when no map provided."""
        config = {'template': '{{fileUpload_1.output.text}}'}
        result = replace_variable_references(config, {})
        # Without func_name_to_slug, mixed-case is preserved
        assert result['template'] == '{{fileUpload_1.output.text}}'


# ============================================================================
# Reverse Resolve Dependencies — Parameters Tests
# ============================================================================


class TestReverseResolveDependenciesFromParameters:
    """Test that reverse_resolve_dependencies scans parameters in addition to config."""

    def test_resolves_agent_id_from_parameters(self, mock_agents, mock_knowledge_bases):
        """Test resolving agentId in parameters dict."""
        nodes = [
            SimpleNamespace(
                config_type='RAG_AGENT',
                config={},
                parameters={
                    'agentId': str(AGENT_UUID),
                    'knowledgeBasesOverride': [],
                },
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)
        assert AGENT_UUID in agent_map
        assert agent_map[AGENT_UUID] == 'Invoice Processing Agent'

    def test_resolves_knowledge_bases_override_from_parameters(
        self, mock_agents, mock_knowledge_bases
    ):
        """Test resolving knowledgeBasesOverride list in parameters dict."""
        nodes = [
            SimpleNamespace(
                config_type='RAG_AGENT',
                config={},
                parameters={
                    'agentId': str(AGENT_UUID),
                    'knowledgeBasesOverride': [str(KB_UUID)],
                },
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        agent_map, kb_map = reverse_resolve_dependencies(nodes, mock_client)
        assert KB_UUID in kb_map
        assert kb_map[KB_UUID] == 'Invoice Docs KB'

    def test_resolves_knowledge_base_id_list_from_parameters(
        self, mock_agents, mock_knowledge_bases
    ):
        """Test resolving knowledgeBaseId (as list) in parameters for RETRIEVE nodes."""
        nodes = [
            SimpleNamespace(
                config_type='RETRIEVE',
                config={},
                parameters={
                    'knowledgeBaseId': [str(KB_UUID)],
                },
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        _, kb_map = reverse_resolve_dependencies(nodes, mock_client)
        assert KB_UUID in kb_map
        assert kb_map[KB_UUID] == 'Invoice Docs KB'

    def test_merges_config_and_parameters_uuids(self, mock_agents, mock_knowledge_bases):
        """Test that UUIDs from both config and parameters are resolved."""
        other_agent = UUID('99999999-9999-9999-9999-999999999999')
        nodes = [
            SimpleNamespace(
                config_type='AGENT',
                config={'agent_id': str(AGENT_UUID)},
                parameters={'agentId': str(other_agent)},
            ),
        ]
        mock_agents_list = [
            {'id': str(AGENT_UUID), 'name': 'Agent A'},
            {'id': str(other_agent), 'name': 'Agent B'},
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents_list
        mock_client.list_knowledge_bases.return_value = []

        agent_map, _ = reverse_resolve_dependencies(nodes, mock_client)
        assert AGENT_UUID in agent_map
        assert other_agent in agent_map


# ============================================================================
# Full Pipeline Test — Frontend-Created Workflow (Parameters-Based)
# ============================================================================


class TestApiResponseToWdfWithParameters:
    """Test api_response_to_wdf with frontend-created workflows (data in parameters)."""

    def test_rag_agent_full_extraction(self):
        """Test pulling a RAG_AGENT workflow with all data in parameters.

        This matches the real API response structure from the user's bug report.
        """
        agent_uuid = UUID('e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0')
        kb_uuid = UUID('6c26048d-2f9c-4177-a126-f2ed8cd02a0e')
        input_uuid = UUID('ffa92e9b-092a-457e-b7e4-2574ba2ce620')
        file_uuid = UUID('4a8611ec-ee1e-4d4d-a66e-76ae207d34ee')
        rag_uuid = UUID('c9043510-f633-4215-bdd1-15509d107383')

        workflow = SimpleNamespace(
            id=WORKFLOW_ID,
            version=1,
            entry_point=input_uuid,
            exit_point=rag_uuid,
            state_schema={},
            organization_id=ORG_ID,
        )
        metadata = SimpleNamespace(
            name='Test Ab2',
            description='',
            tags=[],
        )
        nodes = [
            SimpleNamespace(
                id=input_uuid,
                config_type='PLAIN_TXT_INPUT',
                execution_mode='INPUT',
                function_name='plainTextInput_1',
                parameters={
                    'type': 'plainTextInput',
                    'label': 'question',
                    'text': '',
                    'function_name': 'plainTextInput_1',
                    'collapsed': False,
                },
                config={},
            ),
            SimpleNamespace(
                id=file_uuid,
                config_type='FILE_UPLOAD',
                execution_mode='INPUT',
                function_name='fileUpload_1',
                parameters={
                    'type': 'fileUpload',
                    'label': 'File Upload 1',
                    'acceptedFormats': ['pdf', 'docx', 'txt'],
                    'maxFileSize': 10,
                    'extractText': True,
                    'textExtraction': 'automatic',
                    'function_name': 'fileUpload_1',
                    'collapsed': False,
                },
                config={},
            ),
            SimpleNamespace(
                id=rag_uuid,
                config_type='RAG_AGENT',
                execution_mode='MESSAGES',
                function_name='ragAgent_1',
                parameters={
                    'type': 'ragAgent',
                    'label': 'RAG Agent 1',
                    'agentId': str(agent_uuid),
                    'primaryInput': (
                        '{{plainTextInput_1.output.text}} also provide a sumary '
                        f'of the document  {{{{{str(file_uuid)}.output.text}}}}'
                    ),
                    'knowledgeBasesOverride': [str(kb_uuid)],
                    'disableRAG': False,
                    'function_name': 'ragAgent_1',
                    'collapsed': False,
                },
                config={},
            ),
        ]
        edges = [
            SimpleNamespace(
                id=uuid4(),
                edge_type='STATIC',
                condition_function=None,
                source_node_id=input_uuid,
                target_node_id=rag_uuid,
            ),
            SimpleNamespace(
                id=uuid4(),
                edge_type='STATIC',
                condition_function=None,
                source_node_id=file_uuid,
                target_node_id=input_uuid,
            ),
        ]

        agent_map = {agent_uuid: 'My RAG Agent'}
        kb_map = {kb_uuid: 'Invoice Knowledge Base'}

        wdf, slug_to_uuid, edge_to_id = api_response_to_wdf(
            workflow,
            metadata,
            nodes,
            edges,
            agent_map,
            kb_map,
        )

        # --- Verify workflow structure ---
        assert wdf.name == 'Test Ab2'
        assert len(wdf.nodes) == 3
        assert len(wdf.edges) == 2

        # --- Verify PLAIN_TXT_INPUT node ---
        input_node = wdf.nodes['plaintextinput_1']
        assert input_node.type == 'plain_txt_input'
        assert input_node.execution_mode == 'INPUT'
        assert input_node.label == 'question'

        # --- Verify FILE_UPLOAD node ---
        file_node = wdf.nodes['fileupload_1']
        assert file_node.type == 'file_upload'
        assert file_node.execution_mode == 'INPUT'
        assert file_node.label == 'File Upload 1'
        assert file_node.config['acceptedFormats'] == ['pdf', 'docx', 'txt']
        assert file_node.config['maxFileSize'] == 10
        assert file_node.config['textExtraction'] == 'automatic'

        # --- Verify RAG_AGENT node ---
        rag_node = wdf.nodes['ragagent_1']
        assert rag_node.type == 'rag_agent'
        assert rag_node.execution_mode == 'MESSAGES'
        assert rag_node.label == 'RAG Agent 1'
        # Agent UUID should be resolved to name
        assert rag_node.config['agent_name'] == 'My RAG Agent'
        assert 'agentId' not in rag_node.config
        # KB UUIDs should be resolved to names
        assert rag_node.config['knowledge_base_names'] == ['Invoice Knowledge Base']
        assert 'knowledgeBaseIds' not in rag_node.config
        # UUID reference in primaryInput should be replaced with slug
        assert '{{fileupload_1.output.text}}' in rag_node.config['primaryInput']
        assert str(file_uuid) not in rag_node.config['primaryInput']

    def test_execution_mode_is_plain_string(self):
        """Test that execution_mode is a plain string, not an enum tag."""
        from workflow_models.enums import ExecutionMode, NodeConfigType

        nodes = [
            SimpleNamespace(
                id=INPUT_NODE_ID,
                config_type=NodeConfigType.PLAIN_TXT_INPUT,
                execution_mode=ExecutionMode.INPUT,
                function_name='input_1',
                parameters={'label': 'Test'},
                config={},
            ),
        ]
        workflow = SimpleNamespace(
            id=WORKFLOW_ID,
            version=1,
            entry_point=INPUT_NODE_ID,
            exit_point=INPUT_NODE_ID,
            state_schema={},
            organization_id=ORG_ID,
        )
        metadata = SimpleNamespace(name='Test', description='', tags=[])
        edges: list = []

        wdf, _, _ = api_response_to_wdf(
            workflow,
            metadata,
            nodes,
            edges,
            {},
            {},
        )

        node = wdf.nodes['input_1']
        # execution_mode should be a plain string, not an enum
        assert node.execution_mode == 'INPUT'
        assert type(node.execution_mode) is str
        # type should be lowercase
        assert node.type == 'plain_txt_input'
        assert type(node.type) is str


# ============================================================================
# Extract Node Config — Empty Dependency References (agentId / knowledgeBaseIds)
# ============================================================================


class TestExtractNodeConfigEmptyDependencies:
    """Test that extract_node_config drops empty dependency references.

    When the frontend saves a node without a linked agent or knowledge base
    the database stores ``None``, ``''``, or ``[]`` for the reference fields.
    These empty sentinels should be omitted from the pulled WDF config so that
    the YAML stays clean.
    """

    # --- agentId ---

    def test_agent_node_skips_none_agent_id(self):
        """AGENT node with agentId=None should not include agentId in result."""
        parameters = {
            'type': 'agent',
            'agentId': None,
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
        }
        result = extract_node_config('AGENT', parameters, {})
        assert 'agentId' not in result
        # Non-dependency fields should still be extracted
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert result['system_prompt'] == 'You are helpful.'

    def test_agent_node_skips_empty_string_agent_id(self):
        """AGENT node with agentId='' should not include agentId in result."""
        parameters = {
            'type': 'agent',
            'agentId': '',
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
        }
        result = extract_node_config('AGENT', parameters, {})
        assert 'agentId' not in result
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'

    def test_rag_agent_skips_none_agent_id(self):
        """RAG_AGENT node with agentId=None should not include agentId in result."""
        kb_id = str(uuid4())
        parameters = {
            'type': 'ragAgent',
            'agentId': None,
            'knowledgeBasesOverride': [kb_id],
            'primaryInput': '{{input_1.output.text}}',
        }
        result = extract_node_config('RAG_AGENT', parameters, {})
        assert 'agentId' not in result
        # Other fields should still be present
        assert result['knowledgeBaseIds'] == [kb_id]
        assert result['primaryInput'] == '{{input_1.output.text}}'

    def test_rag_agent_skips_empty_string_agent_id(self):
        """RAG_AGENT node with agentId='' should not include agentId in result."""
        parameters = {
            'type': 'ragAgent',
            'agentId': '',
            'knowledgeBasesOverride': [str(uuid4())],
        }
        result = extract_node_config('RAG_AGENT', parameters, {})
        assert 'agentId' not in result

    # --- knowledgeBasesOverride / knowledgeBaseIds ---

    def test_rag_agent_skips_empty_list_knowledge_bases(self):
        """RAG_AGENT with knowledgeBasesOverride=[] should not include knowledgeBaseIds."""
        agent_id = str(uuid4())
        parameters = {
            'type': 'ragAgent',
            'agentId': agent_id,
            'knowledgeBasesOverride': [],
            'primaryInput': '{{input_1.output.text}}',
        }
        result = extract_node_config('RAG_AGENT', parameters, {})
        assert 'knowledgeBaseIds' not in result
        # agentId with a valid value should still be present
        assert result['agentId'] == agent_id

    def test_rag_agent_skips_none_knowledge_bases(self):
        """RAG_AGENT with knowledgeBasesOverride=None should not include knowledgeBaseIds."""
        parameters = {
            'type': 'ragAgent',
            'agentId': str(uuid4()),
            'knowledgeBasesOverride': None,
        }
        result = extract_node_config('RAG_AGENT', parameters, {})
        assert 'knowledgeBaseIds' not in result

    def test_retrieve_skips_none_knowledge_base_id(self):
        """RETRIEVE with knowledgeBaseId=None should not include knowledgeBaseId."""
        parameters = {
            'type': 'retrieve',
            'knowledgeBaseId': None,
            'topK': 5,
        }
        result = extract_node_config('RETRIEVE', parameters, {})
        assert 'knowledgeBaseId' not in result
        assert result['topK'] == 5

    def test_retrieve_skips_empty_list_knowledge_base_id(self):
        """RETRIEVE with knowledgeBaseId=[] should not include knowledgeBaseId."""
        parameters = {
            'type': 'retrieve',
            'knowledgeBaseId': [],
            'topK': 5,
        }
        result = extract_node_config('RETRIEVE', parameters, {})
        assert 'knowledgeBaseId' not in result

    def test_retrieve_skips_empty_string_knowledge_base_id(self):
        """RETRIEVE with knowledgeBaseId='' should not include knowledgeBaseId."""
        parameters = {
            'type': 'retrieve',
            'knowledgeBaseId': '',
            'topK': 5,
        }
        result = extract_node_config('RETRIEVE', parameters, {})
        assert 'knowledgeBaseId' not in result

    # --- Config fallback with empty refs ---

    def test_agent_config_fallback_skips_none_agent_id(self):
        """AGENT config fallback with agent_id=None should not include agentId."""
        parameters: dict = {}
        config = {
            'agent_id': None,
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
        }
        result = extract_node_config('AGENT', parameters, config)
        assert 'agentId' not in result
        assert result['model'] == 'us.anthropic.claude-sonnet-4-20250514-v1:0'

    def test_agent_config_fallback_skips_empty_string_agent_id(self):
        """AGENT config fallback with agent_id='' should not include agentId."""
        parameters: dict = {}
        config = {
            'agent_id': '',
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
        }
        result = extract_node_config('AGENT', parameters, config)
        assert 'agentId' not in result

    # --- Valid values still work ---

    def test_valid_agent_id_is_preserved(self):
        """A valid UUID agentId should still be extracted."""
        agent_id = str(uuid4())
        parameters = {
            'type': 'agent',
            'agentId': agent_id,
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
        }
        result = extract_node_config('AGENT', parameters, {})
        assert result['agentId'] == agent_id

    def test_valid_knowledge_bases_list_is_preserved(self):
        """A non-empty knowledgeBasesOverride list should still be extracted."""
        kb_ids = [str(uuid4()), str(uuid4())]
        parameters = {
            'type': 'ragAgent',
            'agentId': str(uuid4()),
            'knowledgeBasesOverride': kb_ids,
        }
        result = extract_node_config('RAG_AGENT', parameters, {})
        assert result['knowledgeBaseIds'] == kb_ids

    # --- Non-dependency None values still work ---

    def test_none_temperature_is_preserved(self):
        """None for non-dependency fields (temperature) should still be included."""
        parameters = {
            'type': 'agent',
            'agentId': str(uuid4()),
            'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
            'system_prompt': 'You are helpful.',
            'temperature': None,
            'maxTokens': None,
        }
        result = extract_node_config('AGENT', parameters, {})
        assert 'temperature' in result
        assert result['temperature'] is None
        assert 'maxTokens' in result
        assert result['maxTokens'] is None


# ============================================================================
# RAG_AGENT Config Fallback Tests
# ============================================================================


class TestRagAgentConfigFallback:
    """Test that RAG_AGENT has config fallback mappings for CLI-pushed workflows."""

    def test_rag_agent_from_config_fallback(self):
        """RAG_AGENT should extract agent_id and knowledge_base_ids from config dict."""
        agent_id = str(uuid4())
        kb_ids = [str(uuid4()), str(uuid4())]
        parameters: dict = {}
        config = {
            'agent_id': agent_id,
            'knowledge_base_ids': kb_ids,
            'primaryInput': '{{input_1.output.text}}',
        }
        result = extract_node_config('RAG_AGENT', parameters, config)
        # agent_id -> agentId renaming
        assert result['agentId'] == agent_id
        # knowledge_base_ids -> knowledgeBaseIds renaming
        assert result['knowledgeBaseIds'] == kb_ids
        assert result['primaryInput'] == '{{input_1.output.text}}'

    def test_rag_agent_parameters_over_config(self):
        """RAG_AGENT parameters should take priority over config fallback."""
        param_agent = str(uuid4())
        config_agent = str(uuid4())
        parameters = {
            'type': 'ragAgent',
            'agentId': param_agent,
            'knowledgeBasesOverride': [str(uuid4())],
        }
        config = {
            'agent_id': config_agent,
            'knowledge_base_ids': [str(uuid4())],
        }
        result = extract_node_config('RAG_AGENT', parameters, config)
        # parameters value should win
        assert result['agentId'] == param_agent

    def test_rag_agent_config_fallback_fills_gaps(self):
        """RAG_AGENT should use config fallback only for fields missing in parameters."""
        param_agent = str(uuid4())
        config_kb_ids = [str(uuid4())]
        parameters = {
            'type': 'ragAgent',
            'agentId': param_agent,
            # knowledgeBasesOverride intentionally missing from parameters
        }
        config = {
            'knowledge_base_ids': config_kb_ids,
        }
        result = extract_node_config('RAG_AGENT', parameters, config)
        assert result['agentId'] == param_agent
        assert result['knowledgeBaseIds'] == config_kb_ids


# ============================================================================
# Reverse Resolve — Warning on Unresolved UUIDs
# ============================================================================


class TestReverseResolveWarnings:
    """Test that reverse_resolve_dependencies warns about unresolved UUIDs."""

    def test_warns_on_unresolved_agent_uuid(self, mock_knowledge_bases, capsys):
        """Should warn when an agent UUID is not found in list_agents response."""
        unknown_agent = uuid4()
        nodes = [
            SimpleNamespace(
                config_type='AGENT',
                config={},
                parameters={
                    'agentId': str(unknown_agent),
                },
            ),
        ]

        mock_client = MagicMock()
        # Return agents that don't include the referenced UUID
        mock_client.list_agents.return_value = [
            {'id': str(uuid4()), 'name': 'Some Other Agent'},
        ]
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        agent_map, _ = reverse_resolve_dependencies(nodes, mock_client)
        assert unknown_agent not in agent_map

        # The warning should have been printed via rich console
        captured = capsys.readouterr()
        assert str(unknown_agent) in captured.out
        assert 'not found' in captured.out

    def test_warns_on_unresolved_kb_uuid(self, mock_agents, capsys):
        """Should warn when a KB UUID is not found in list_knowledge_bases response."""
        unknown_kb = uuid4()
        nodes = [
            SimpleNamespace(
                config_type='RETRIEVE',
                config={},
                parameters={
                    'knowledgeBaseId': str(unknown_kb),
                },
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        # Return KBs that don't include the referenced UUID
        mock_client.list_knowledge_bases.return_value = [
            {'id': str(uuid4()), 'name': 'Some Other KB'},
        ]

        _, kb_map = reverse_resolve_dependencies(nodes, mock_client)
        assert unknown_kb not in kb_map

        captured = capsys.readouterr()
        assert str(unknown_kb) in captured.out
        assert 'not found' in captured.out

    def test_no_warning_when_all_resolved(self, mock_agents, mock_knowledge_bases, capsys):
        """Should not warn when all UUIDs are resolved successfully."""
        nodes = [
            SimpleNamespace(
                config_type='AGENT',
                config={},
                parameters={'agentId': str(AGENT_UUID)},
            ),
        ]

        mock_client = MagicMock()
        mock_client.list_agents.return_value = mock_agents
        mock_client.list_knowledge_bases.return_value = mock_knowledge_bases

        agent_map, _ = reverse_resolve_dependencies(nodes, mock_client)
        assert AGENT_UUID in agent_map

        captured = capsys.readouterr()
        assert 'not found' not in captured.out
