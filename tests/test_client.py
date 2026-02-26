"""Tests for the WorkflowClient API client.

RAG-946: Phase 1 — API Client (httpx-Based Platform Client)

Covers all five BDD scenarios from the Jira ticket:
  1. Client authenticates via API key
  2. Client calls atomic save endpoint
  3. Client resolves agent by name
  4. Client handles errors gracefully
  5. Client executes workflow via Temporal
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from cli.client import (
    SaveCompleteWorkflowResponse,
    SubmitInputResponse,
    SubmitReviewResponse,
    TemporalStartResponse,
    WorkflowClient,
    WorkflowStatusResponse,
)
from cli.config import CLIConfig
from cli.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ServerError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

BASE_URL = 'https://api.example.com'
API_KEY = 'test-api-key-12345'
ORG_ID = '00000000-0000-0000-0000-000000000001'

WORKFLOW_ID = '11111111-1111-1111-1111-111111111111'
NODE_ID = '22222222-2222-2222-2222-222222222222'
RUN_ID = 'run-abc-123'


@pytest.fixture()
def client() -> WorkflowClient:
    """Provide a WorkflowClient instance for testing."""
    c = WorkflowClient(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID)
    yield c
    c.close()


# ============================================================================
# BDD Scenario 1: Client authenticates via API key
# ============================================================================


class TestClientAuthentication:
    """
    Scenario: Client authenticates via API key
      Given valid API credentials
      When any request is made
      Then the X-API-Key header is sent with every request
    """

    def test_api_key_header_sent_on_get(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            json=[],
        )
        client.list_workflows()
        request = httpx_mock.get_requests()[0]
        assert request.headers['x-api-key'] == API_KEY

    def test_api_key_header_sent_on_post(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json={
                'workflow_id': WORKFLOW_ID,
                'run_id': RUN_ID,
                'temporal_workflow_id': 'temporal-123',
                'status': 'started',
                'message': 'Workflow started',
            },
        )
        client.start_workflow_temporal(WORKFLOW_ID)
        request = httpx_mock.get_requests()[0]
        assert request.headers['x-api-key'] == API_KEY

    def test_api_key_header_sent_on_delete(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/{WORKFLOW_ID}',
            status_code=204,
        )
        client.delete_workflow(WORKFLOW_ID)
        request = httpx_mock.get_requests()[0]
        assert request.headers['x-api-key'] == API_KEY

    def test_base_url_used_for_all_requests(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            json=[],
        )
        client.list_workflows()
        request = httpx_mock.get_requests()[0]
        assert str(request.url).startswith(BASE_URL)


# ============================================================================
# BDD Scenario 2: Client calls atomic save endpoint
# ============================================================================


class TestSaveCompleteWorkflow:
    """
    Scenario: Client calls atomic save endpoint
      Given a complete workflow payload
      When client.save_complete_workflow(payload) is called
      Then POST /v1/workflows/complete is called with the payload
      And the response is parsed into typed models
    """

    COMPLETE_RESPONSE = {
        'workflow': {
            'id': WORKFLOW_ID,
            'version': 1,
            'entry_point': NODE_ID,
            'exit_point': NODE_ID,
            'state_schema': {},
            'execution_config': {},
            'organization_id': ORG_ID,
            'created_by': ORG_ID,
            'created_at': '2026-01-01T00:00:00',
            'updated_at': '2026-01-01T00:00:00',
        },
        'nodes': [],
        'node_inputs': [],
        'node_outputs': [],
        'edges': [],
        'workflow_visuals': None,
        'node_visuals': [],
        'edge_visuals': [],
        'metadata': None,
        'created': True,
    }

    def test_save_complete_workflow_create(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/complete',
            status_code=201,
            json=self.COMPLETE_RESPONSE,
        )
        payload = {'workflow': {'version': 1, 'organization_id': ORG_ID}, 'nodes': [], 'edges': []}
        result = client.save_complete_workflow(payload)

        # Verify request
        request = httpx_mock.get_requests()[0]
        assert request.method == 'POST'
        assert str(request.url) == f'{BASE_URL}/v1/workflows/complete'

        # Verify response typing
        assert isinstance(result, SaveCompleteWorkflowResponse)
        assert result.created is True
        assert str(result.workflow.id) == WORKFLOW_ID

    def test_save_complete_workflow_update(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        update_response = {**self.COMPLETE_RESPONSE, 'created': False}
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/complete',
            status_code=200,
            json=update_response,
        )
        payload = {
            'workflow_id': WORKFLOW_ID,
            'workflow': {'version': 2, 'organization_id': ORG_ID},
            'nodes': [],
            'edges': [],
        }
        result = client.save_complete_workflow(payload)
        assert isinstance(result, SaveCompleteWorkflowResponse)
        assert result.created is False

    def test_save_complete_workflow_validation_error(
        self, client: WorkflowClient, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/complete',
            status_code=400,
            json={'detail': 'Invalid edge reference'},
        )
        with pytest.raises(ValidationError) as exc_info:
            client.save_complete_workflow({'workflow': {}, 'nodes': [], 'edges': []})
        assert exc_info.value.status_code == 400


# ============================================================================
# BDD Scenario 3: Client resolves agent by name
# ============================================================================


class TestAgentResolution:
    """
    Scenario: Client resolves agent by name
      Given an agent name "Invoice Processing Agent"
      When client.find_agent_by_name(name) is called
      Then the API is queried for matching agents
      And the UUID is returned if found, None if not
    """

    AGENTS_RESPONSE = [
        {
            'id': '33333333-3333-3333-3333-333333333333',
            'name': 'Invoice Processing Agent',
            'description': 'Processes invoices',
            'model': 'gpt-4',
            'system_prompt': 'You are an invoice processor.',
            'temperature': 0.7,
            'max_tokens': None,
            'top_p': None,
            'is_active': True,
            'organization_id': ORG_ID,
            'created_by': ORG_ID,
            'created_at': '2026-01-01T00:00:00',
            'updated_at': '2026-01-01T00:00:00',
            'knowledge_base_ids': [],
        },
        {
            'id': '44444444-4444-4444-4444-444444444444',
            'name': 'Customer Support Agent',
            'description': 'Handles support',
            'model': 'gpt-4',
            'system_prompt': 'You are a support agent.',
            'temperature': 0.5,
            'max_tokens': None,
            'top_p': None,
            'is_active': True,
            'organization_id': ORG_ID,
            'created_by': ORG_ID,
            'created_at': '2026-01-01T00:00:00',
            'updated_at': '2026-01-01T00:00:00',
            'knowledge_base_ids': [],
        },
    ]

    def test_find_agent_by_name_found(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/agents/', params={'offset': '0', 'limit': '100'}),
            json=self.AGENTS_RESPONSE,
        )
        result = client.find_agent_by_name('Invoice Processing Agent')
        assert result is not None
        assert result['name'] == 'Invoice Processing Agent'
        assert result['id'] == '33333333-3333-3333-3333-333333333333'

    def test_find_agent_by_name_not_found(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/agents/', params={'offset': '0', 'limit': '100'}),
            json=self.AGENTS_RESPONSE,
        )
        result = client.find_agent_by_name('Nonexistent Agent')
        assert result is None

    def test_find_agent_by_name_case_insensitive(
        self, client: WorkflowClient, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/agents/', params={'offset': '0', 'limit': '100'}),
            json=self.AGENTS_RESPONSE,
        )
        result = client.find_agent_by_name('invoice processing agent')
        assert result is not None
        assert result['name'] == 'Invoice Processing Agent'


class TestKnowledgeBaseResolution:
    """Client resolves knowledge base by name using client-side filtering."""

    KB_RESPONSE = [
        {
            'id': '55555555-5555-5555-5555-555555555555',
            'name': 'Product Docs',
            'description': 'Product documentation',
            'organization_id': ORG_ID,
            'status': 'READY',
            'embedding_model': 'amazon.titan-embed-text-v2:0',
            'chunking_strategy': 'FIXED',
            'chunk_size': 512,
            'chunk_overlap': 50,
            'top_k_results': 5,
            'similarity_threshold': 0.7,
            'knowledge_base_remote_id': None,
            'data_source_id': None,
            'total_documents': None,
            'total_chunks': None,
            'created_by': ORG_ID,
            'created_at': '2026-01-01T00:00:00',
            'updated_at': '2026-01-01T00:00:00',
        },
    ]

    def test_find_kb_by_name_found(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                BASE_URL + '/v1/knowledge_bases/', params={'offset': '0', 'limit': '100'}
            ),
            json=self.KB_RESPONSE,
        )
        result = client.find_knowledge_base_by_name('Product Docs')
        assert result is not None
        assert result['name'] == 'Product Docs'

    def test_find_kb_by_name_not_found(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                BASE_URL + '/v1/knowledge_bases/', params={'offset': '0', 'limit': '100'}
            ),
            json=self.KB_RESPONSE,
        )
        result = client.find_knowledge_base_by_name('Nonexistent KB')
        assert result is None


# ============================================================================
# BDD Scenario 4: Client handles errors gracefully
# ============================================================================


class TestErrorHandling:
    """
    Scenario: Client handles errors gracefully
      Given the API returns a 4xx or 5xx error
      When any client method is called
      Then a typed exception is raised with the error detail
      And the HTTP status code is preserved
    """

    def test_400_raises_validation_error(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            status_code=400,
            json={'detail': 'Bad request'},
        )
        with pytest.raises(ValidationError) as exc_info:
            client.list_workflows()
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'Bad request'

    def test_401_raises_authentication_error(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            status_code=401,
            json={'detail': 'Could not validate credentials'},
        )
        with pytest.raises(AuthenticationError) as exc_info:
            client.list_workflows()
        assert exc_info.value.status_code == 401

    def test_403_raises_authorization_error(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            status_code=403,
            json={'detail': 'Access denied'},
        )
        with pytest.raises(AuthorizationError):
            client.list_workflows()

    def test_404_raises_not_found_error(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/{WORKFLOW_ID}',
            status_code=404,
            json={'detail': 'Workflow not found'},
        )
        with pytest.raises(NotFoundError) as exc_info:
            client.get_workflow(WORKFLOW_ID)
        assert exc_info.value.detail == 'Workflow not found'

    def test_500_raises_server_error(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            status_code=500,
            json={'detail': 'Internal server error'},
        )
        with pytest.raises(ServerError) as exc_info:
            client.list_workflows()
        assert exc_info.value.status_code == 500

    def test_error_preserves_detail(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/{WORKFLOW_ID}',
            status_code=404,
            json={'detail': 'Workflow abc-123 not found'},
        )
        with pytest.raises(APIError) as exc_info:
            client.get_workflow(WORKFLOW_ID)
        assert exc_info.value.detail == 'Workflow abc-123 not found'
        assert exc_info.value.status_code == 404


# ============================================================================
# BDD Scenario 5: Client executes workflow via Temporal
# ============================================================================


class TestTemporalExecution:
    """
    Scenario: Client executes workflow via Temporal
      Given a workflow ID
      When client.start_workflow_temporal(workflow_id, inputs) is called
      Then POST /v2/workflows/{id}/start/temporal is called
      And the run_id is returned
    """

    START_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'run_id': RUN_ID,
        'temporal_workflow_id': 'temporal-wf-123',
        'status': 'started',
        'message': 'Workflow started successfully',
    }

    STATUS_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'run_id': RUN_ID,
        'status': 'running',
        'current_node': NODE_ID,
        'state': {'inputs': {}},
    }

    INPUT_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'node_id': NODE_ID,
        'status': 'submitted',
        'message': 'Input submitted successfully',
    }

    REVIEW_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'status': 'submitted',
        'message': 'Review submitted successfully',
    }

    def test_start_workflow_temporal(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json=self.START_RESPONSE,
        )
        result = client.start_workflow_temporal(WORKFLOW_ID)

        # Verify request
        request = httpx_mock.get_requests()[0]
        assert request.method == 'POST'
        assert str(request.url) == f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal'

        # Verify response
        assert isinstance(result, TemporalStartResponse)
        assert result.run_id == RUN_ID
        assert result.status == 'started'

    def test_start_workflow_temporal_with_inputs(
        self, client: WorkflowClient, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json=self.START_RESPONSE,
        )
        inputs = {NODE_ID: {'text': 'Hello world'}}
        client.start_workflow_temporal(WORKFLOW_ID, inputs=inputs)

        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body['state']['inputs'] == inputs

    def test_get_workflow_status(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/status',
                params={'run_id': RUN_ID},
            ),
            json=self.STATUS_RESPONSE,
        )
        result = client.get_workflow_status(WORKFLOW_ID, RUN_ID)

        assert isinstance(result, WorkflowStatusResponse)
        assert result.status == 'running'
        assert result.current_node == NODE_ID

    def test_submit_input(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/input',
                params={'run_id': RUN_ID},
            ),
            json=self.INPUT_RESPONSE,
        )
        result = client.submit_input(
            WORKFLOW_ID,
            run_id=RUN_ID,
            node_id=NODE_ID,
            input_data={'text': 'User input here'},
        )

        assert isinstance(result, SubmitInputResponse)
        assert result.status == 'submitted'

        # Verify request body
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body['node_id'] == NODE_ID
        assert body['input_data'] == {'text': 'User input here'}

    def test_submit_review(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/review',
                params={'run_id': RUN_ID},
            ),
            json=self.REVIEW_RESPONSE,
        )
        result = client.submit_review(
            WORKFLOW_ID,
            run_id=RUN_ID,
            decision='approve',
            feedback='Looks good',
        )

        assert isinstance(result, SubmitReviewResponse)
        assert result.status == 'submitted'

        # Verify request body
        request = httpx_mock.get_requests()[0]
        import json

        body = json.loads(request.content)
        assert body['decision'] == 'approve'
        assert body['feedback'] == 'Looks good'


# ============================================================================
# V1 CRUD Operations
# ============================================================================


class TestV1CRUDOperations:
    """Tests for V1 workflow CRUD endpoint wrappers."""

    WORKFLOW_RESPONSE = {
        'id': WORKFLOW_ID,
        'version': 1,
        'entry_point': NODE_ID,
        'exit_point': NODE_ID,
        'state_schema': {},
        'execution_config': {},
        'organization_id': ORG_ID,
        'created_by': ORG_ID,
        'created_at': '2026-01-01T00:00:00',
        'updated_at': '2026-01-01T00:00:00',
    }

    NODE_RESPONSE = [
        {
            'id': NODE_ID,
            'workflow_id': WORKFLOW_ID,
            'workflow_version': 1,
            'config_type': 'LLM_CALL',
            'execution_mode': 'MESSAGES',
            'function_name': None,
            'parameters': {},
            'retry_policy': {'max_retries': 3},
            'timeout_seconds': 30,
            'config': {},
            'delegated_response': False,
            'step_type': 'STEP',
            'join_config': {},
            'created_at': '2026-01-01T00:00:00',
        }
    ]

    EDGE_RESPONSE = [
        {
            'id': '66666666-6666-6666-6666-666666666666',
            'workflow_id': WORKFLOW_ID,
            'workflow_version': 1,
            'edge_type': 'STATIC',
            'condition_function': None,
            'data_mapping': {},
            'source_node_id': NODE_ID,
            'target_node_id': NODE_ID,
            'created_at': '2026-01-01T00:00:00',
        }
    ]

    METADATA_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'owner_id': ORG_ID,
        'name': 'Test Workflow',
        'description': 'A test workflow',
        'tags': ['test'],
        'is_active': True,
        'custom_fields': {},
        'created_at': '2026-01-01T00:00:00',
        'updated_at': '2026-01-01T00:00:00',
    }

    def test_list_workflows(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            json=[self.WORKFLOW_RESPONSE],
        )
        result = client.list_workflows()

        from workflow_models import WorkflowPublic

        assert len(result) == 1
        assert isinstance(result[0], WorkflowPublic)
        assert str(result[0].id) == WORKFLOW_ID

    def test_list_workflows_with_org_filter(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                BASE_URL + '/v1/workflows/',
                params={'organization_id': ORG_ID, 'offset': '0', 'limit': '100'},
            ),
            json=[self.WORKFLOW_RESPONSE],
        )
        client.list_workflows(organization_id=ORG_ID)
        request = httpx_mock.get_requests()[0]
        assert 'organization_id' in str(request.url)

    def test_get_workflow(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/{WORKFLOW_ID}',
            json=self.WORKFLOW_RESPONSE,
        )
        from workflow_models import WorkflowPublic

        result = client.get_workflow(WORKFLOW_ID)
        assert isinstance(result, WorkflowPublic)

    def test_delete_workflow(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflows/{WORKFLOW_ID}',
            status_code=204,
        )
        client.delete_workflow(WORKFLOW_ID)  # Should not raise
        request = httpx_mock.get_requests()[0]
        assert request.method == 'DELETE'

    def test_list_nodes(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                BASE_URL + '/v1/workflow-nodes/',
                params={'workflow_id': WORKFLOW_ID, 'offset': '0', 'limit': '100'},
            ),
            json=self.NODE_RESPONSE,
        )
        from workflow_models import LogicalNodePublic

        result = client.list_nodes(WORKFLOW_ID)
        assert len(result) == 1
        assert isinstance(result[0], LogicalNodePublic)

    def test_list_edges(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(
                BASE_URL + '/v1/workflow-edges/',
                params={'workflow_id': WORKFLOW_ID, 'offset': '0', 'limit': '100'},
            ),
            json=self.EDGE_RESPONSE,
        )
        from workflow_models import LogicalEdgePublic

        result = client.list_edges(WORKFLOW_ID)
        assert len(result) == 1
        assert isinstance(result[0], LogicalEdgePublic)

    def test_get_metadata(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f'{BASE_URL}/v1/workflow-metadata/{WORKFLOW_ID}',
            json=self.METADATA_RESPONSE,
        )
        from workflow_models import WorkflowMetadataPublic

        result = client.get_metadata(WORKFLOW_ID)
        assert isinstance(result, WorkflowMetadataPublic)
        assert result.name == 'Test Workflow'


# ============================================================================
# Client lifecycle and configuration
# ============================================================================


class TestClientLifecycle:
    """Tests for client construction, configuration, and cleanup."""

    def test_from_config_factory(self):
        config = CLIConfig(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID)
        client = WorkflowClient.from_config(config)
        assert client._org_id == ORG_ID
        client.close()

    def test_from_config_validates(self):
        config = CLIConfig(host=None, api_key=None, org_id=None)
        from cli.config import ConfigError

        with pytest.raises(ConfigError):
            WorkflowClient.from_config(config)

    def test_context_manager(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE_URL + '/v1/workflows/', params={'offset': '0', 'limit': '100'}),
            json=[],
        )
        with WorkflowClient(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID) as client:
            client.list_workflows()
        # Client should be closed after context manager exits

    def test_host_trailing_slash_stripped(self):
        client = WorkflowClient(host='https://api.example.com/', api_key=API_KEY, org_id=ORG_ID)
        assert client._base_url == 'https://api.example.com'
        client.close()

    def test_custom_timeout(self):
        client = WorkflowClient(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID, timeout=60.0)
        client.close()


# ============================================================================
# AUDIT-04: V2 Temporal Endpoints Use X-API-Key Authentication
# ============================================================================


class TestV2TemporalApiKeyAuth:
    """Verify that V2 Temporal endpoints use X-API-Key header for authentication.

    The backend's get_authenticated_user dependency accepts both JWT and API key
    with a fall-through pattern (try JWT first, then API key). The CLI uses
    X-API-Key exclusively, which is sufficient for all V2 endpoints.

    Verified via backend source: authentication_manager.py:391

    These tests confirm the client's auth mechanism without needing a live API.
    AUDIT-04 requires proof that the existing X-API-Key approach works for V2.
    """

    def test_v2_temporal_uses_api_key_header(self):
        """V2 Temporal endpoints use X-API-Key header for authentication.

        The backend's get_authenticated_user dependency accepts both JWT and API key
        with a fall-through pattern (try JWT first, then API key). The CLI uses
        X-API-Key exclusively, which is sufficient for all V2 endpoints.

        Verified via backend source: authentication_manager.py:391
        """
        client = WorkflowClient(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID)
        assert client._client.headers['x-api-key'] == API_KEY
        client.close()

    def test_v2_start_temporal_sends_request_to_correct_endpoint(
        self, client: WorkflowClient, httpx_mock: HTTPXMock
    ):
        """V2 start/temporal sends POST with X-API-Key to correct V2 endpoint."""
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json={
                'workflow_id': WORKFLOW_ID,
                'run_id': RUN_ID,
                'temporal_workflow_id': 'temporal-123',
                'status': 'started',
                'message': 'Workflow started',
            },
        )
        client.start_workflow_temporal(WORKFLOW_ID)

        request = httpx_mock.get_requests()[0]
        assert request.method == 'POST'
        assert '/v2/workflows/' in str(request.url)
        assert '/start/temporal' in str(request.url)
        assert request.headers['x-api-key'] == API_KEY

    def test_v2_status_sends_api_key_header(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        """V2 status endpoint sends GET with X-API-Key header."""
        httpx_mock.add_response(
            url=httpx.URL(
                f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/status',
                params={'run_id': RUN_ID},
            ),
            json={
                'workflow_id': WORKFLOW_ID,
                'run_id': RUN_ID,
                'status': 'running',
                'current_node': NODE_ID,
                'state': {},
            },
        )
        client.get_workflow_status(WORKFLOW_ID, RUN_ID)

        request = httpx_mock.get_requests()[0]
        assert request.method == 'GET'
        assert '/v2/workflows/' in str(request.url)
        assert '/status' in str(request.url)
        assert request.headers['x-api-key'] == API_KEY
