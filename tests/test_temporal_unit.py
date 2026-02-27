"""Unit tests for run_id camelCase aliasing and Temporal response model parsing.

Phase 08-01: Validates that the CLI sends 'runId' (camelCase) in the request
body and correctly parses snake_case 'run_id' from backend JSON responses.

Requirements: TD-03, TD-05
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from cli.client import (
    TemporalStartResponse,
    WorkflowClient,
    WorkflowStatusResponse,
)

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

BASE_URL = 'https://test.example.com'
API_KEY = 'test-key'
ORG_ID = 'test-org'
WORKFLOW_ID = '11111111-1111-1111-1111-111111111111'


@pytest.fixture()
def client() -> WorkflowClient:
    """Provide a WorkflowClient instance for testing."""
    c = WorkflowClient(host=BASE_URL, api_key=API_KEY, org_id=ORG_ID)
    yield c
    c.close()


# ============================================================================
# TD-03 / TD-05: run_id camelCase aliasing and response model parsing
# ============================================================================


class TestRunIdAliasing:
    """Validate run_id camelCase wire format and snake_case response parsing.

    TD-03: Client sends 'runId' (camelCase) in the POST request body.
    TD-05: Response models parse 'run_id' (snake_case) from backend JSON.
    """

    MOCK_START_RESPONSE = {
        'workflow_id': WORKFLOW_ID,
        'run_id': 'run-mock-123',
        'temporal_workflow_id': 'temporal-wf-mock',
        'status': 'started',
        'message': 'Workflow started successfully',
    }

    def test_start_workflow_sends_camel_case_run_id(
        self, client: WorkflowClient, httpx_mock: HTTPXMock
    ):
        """Request body must contain 'runId' (camelCase), NOT 'run_id'.

        The backend expects AG-UI wire format with camelCase keys.
        This validates the client serialization matches the contract.
        """
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json=self.MOCK_START_RESPONSE,
        )
        client.start_workflow_temporal(WORKFLOW_ID)

        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)

        # Must use camelCase 'runId' in wire format
        assert 'runId' in body, "Request body must contain 'runId' (camelCase)"
        assert 'run_id' not in body, "Request body must NOT contain 'run_id' (snake_case)"

        # runId value must be a non-empty string (UUID)
        assert isinstance(body['runId'], str)
        assert len(body['runId']) > 0

    def test_temporal_start_response_parses_snake_case(self):
        """TemporalStartResponse must parse 'run_id' from backend JSON.

        The backend returns snake_case field names. The Pydantic model
        must correctly map these to Python attributes.
        """
        resp = TemporalStartResponse.model_validate(
            {
                'workflow_id': 'wf-1',
                'run_id': 'run-123',
                'temporal_workflow_id': 'twf-1',
                'status': 'started',
                'message': 'ok',
            }
        )
        assert resp.run_id == 'run-123'
        assert resp.workflow_id == 'wf-1'
        assert resp.temporal_workflow_id == 'twf-1'
        assert resp.status == 'started'
        assert resp.message == 'ok'

    def test_workflow_status_response_parses_snake_case(self):
        """WorkflowStatusResponse must parse 'run_id' from backend JSON.

        Validates the status endpoint response model handles snake_case
        field names correctly, including optional fields.
        """
        resp = WorkflowStatusResponse.model_validate(
            {
                'workflow_id': 'wf-1',
                'run_id': 'run-456',
                'status': 'RUNNING',
            }
        )
        assert resp.run_id == 'run-456'
        assert resp.workflow_id == 'wf-1'
        assert resp.status == 'RUNNING'
        # Optional fields should default correctly
        assert resp.current_node is None
        assert resp.state == {}

    def test_start_workflow_round_trip_mock(self, client: WorkflowClient, httpx_mock: HTTPXMock):
        """Full round-trip: client sends camelCase, receives snake_case.

        Mock the POST endpoint to return snake_case 'run_id', call
        start_workflow_temporal(), and verify the returned model has
        the correct run_id value. This proves the full
        serialization/deserialization path works end-to-end.
        """
        expected_run_id = 'run-round-trip-789'
        httpx_mock.add_response(
            url=f'{BASE_URL}/v2/workflows/{WORKFLOW_ID}/start/temporal',
            json={
                'workflow_id': WORKFLOW_ID,
                'run_id': expected_run_id,
                'temporal_workflow_id': 'temporal-round-trip',
                'status': 'started',
                'message': 'Round trip test',
            },
        )

        result = client.start_workflow_temporal(WORKFLOW_ID)

        # Verify the returned model parsed the run_id correctly
        assert isinstance(result, TemporalStartResponse)
        assert result.run_id == expected_run_id
        assert result.workflow_id == WORKFLOW_ID
        assert result.status == 'started'

        # Also verify the request used camelCase
        request = httpx_mock.get_requests()[0]
        body = json.loads(request.content)
        assert 'runId' in body
