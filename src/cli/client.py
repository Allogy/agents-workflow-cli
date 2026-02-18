"""HTTP API client for the Agents Workflow Platform.

Provides ``WorkflowClient``, an httpx-based synchronous client that
communicates with the platform's REST API using X-API-Key authentication.

Supports:
  - V1 workflow CRUD operations
  - V1 atomic save (POST /v1/workflows/complete)
  - V2 Temporal workflow execution (start, status, input, review)
  - Dependency resolution (agents, knowledge bases)

Usage::

    from cli.client import WorkflowClient

    with WorkflowClient(host="https://api.example.com", api_key="key", org_id="org") as client:
        workflows = client.list_workflows()
        result = client.start_workflow_temporal(workflow_id, inputs={...})
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from workflow_models import (
    EdgeVisualsPublic,
    LogicalEdgePublic,
    LogicalNodeInputPublic,
    LogicalNodeOutputPublic,
    LogicalNodePublic,
    NodeVisualsPublic,
    WorkflowMetadataPublic,
    WorkflowPublic,
    WorkflowVisualsPublic,
)

from cli.config import CLIConfig
from cli.exceptions import raise_for_status

# ---------------------------------------------------------------------------
# Response models for V2 Temporal endpoints
# (not yet in shared-models — defined locally)
# ---------------------------------------------------------------------------


class TemporalStartResponse(BaseModel):
    """Response from POST /v2/workflows/{id}/start/temporal."""

    workflow_id: str
    run_id: str
    temporal_workflow_id: str
    status: str
    message: str


class WorkflowStatusResponse(BaseModel):
    """Response from GET /v2/workflows/{id}/status."""

    workflow_id: str
    run_id: str
    status: str
    current_node: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)


class SubmitInputResponse(BaseModel):
    """Response from POST /v2/workflows/{id}/input."""

    workflow_id: str
    node_id: str
    status: str
    message: str


class SubmitReviewResponse(BaseModel):
    """Response from POST /v2/workflows/{id}/review."""

    workflow_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Response model for the atomic save endpoint
# ---------------------------------------------------------------------------


class SaveCompleteWorkflowResponse(BaseModel):
    """Response from POST /v1/workflows/complete.

    Uses the shared-model ``*Public`` schemas for fully typed sub-entities.
    """

    workflow: WorkflowPublic
    nodes: list[LogicalNodePublic]
    node_inputs: list[LogicalNodeInputPublic]
    node_outputs: list[LogicalNodeOutputPublic]
    edges: list[LogicalEdgePublic]
    workflow_visuals: WorkflowVisualsPublic | None = None
    node_visuals: list[NodeVisualsPublic] = Field(default_factory=list)
    edge_visuals: list[EdgeVisualsPublic] = Field(default_factory=list)
    metadata: WorkflowMetadataPublic | None = None
    created: bool = Field(description='True if the workflow was created, False if updated')


# ---------------------------------------------------------------------------
# WorkflowClient
# ---------------------------------------------------------------------------


class WorkflowClient:
    """Synchronous HTTP client for the Agents Workflow Platform API.

    All requests include an ``X-API-Key`` header for authentication.
    Responses are validated and parsed into typed Pydantic models.
    HTTP errors are mapped to typed exceptions via :func:`cli.exceptions.raise_for_status`.

    Args:
        host: Base URL of the platform API (e.g. ``https://api.example.com``).
        api_key: API key for authentication.
        org_id: Default organization ID for scoped operations.
        timeout: Request timeout in seconds (default 30).
    """

    def __init__(
        self,
        host: str,
        api_key: str,
        org_id: str,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = host.rstrip('/')
        self._api_key = api_key
        self._org_id = org_id
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={'X-API-Key': api_key},
            timeout=timeout,
        )

    @classmethod
    def from_config(cls, config: CLIConfig) -> WorkflowClient:
        """Create a client from a resolved CLI configuration.

        Validates that the configuration contains all required API credentials
        before constructing the client.

        Raises:
            ConfigError: If host, api_key, or org_id are missing.
        """
        config.validate_for_api()
        return cls(
            host=config.host,  # type: ignore[arg-type]
            api_key=config.api_key,  # type: ignore[arg-type]
            org_id=config.org_id,  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> WorkflowClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        """Send a GET request and raise on error."""
        response = self._client.get(path, params=params)
        raise_for_status(response)
        return response

    def _post(
        self, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Send a POST request and raise on error."""
        response = self._client.post(path, json=json, params=params)
        raise_for_status(response)
        return response

    def _delete(self, path: str) -> httpx.Response:
        """Send a DELETE request and raise on error."""
        response = self._client.delete(path)
        raise_for_status(response)
        return response

    # ==================================================================
    # V1 Workflow CRUD
    # ==================================================================

    def list_workflows(
        self,
        organization_id: str | None = None,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[WorkflowPublic]:
        """List workflows, optionally filtered by organization.

        Args:
            organization_id: Filter to a specific organization. If None, returns
                all workflows accessible to the API key.
            offset: Pagination offset.
            limit: Maximum number of results (max 100).

        Returns:
            List of workflow objects.
        """
        params: dict[str, Any] = {'offset': offset, 'limit': limit}
        if organization_id is not None:
            params['organization_id'] = organization_id
        response = self._get('/v1/workflows/', params=params)
        return [WorkflowPublic.model_validate(w) for w in response.json()]

    def get_workflow(self, workflow_id: str | UUID) -> WorkflowPublic:
        """Get a single workflow by ID.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The workflow object.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        response = self._get(f'/v1/workflows/{workflow_id}')
        return WorkflowPublic.model_validate(response.json())

    def delete_workflow(self, workflow_id: str | UUID) -> None:
        """Delete a workflow by ID.

        Args:
            workflow_id: UUID of the workflow to delete.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        self._delete(f'/v1/workflows/{workflow_id}')

    def list_nodes(
        self,
        workflow_id: str | UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[LogicalNodePublic]:
        """List all nodes for a workflow.

        Args:
            workflow_id: UUID of the workflow.
            offset: Pagination offset.
            limit: Maximum number of results.

        Returns:
            List of node objects.
        """
        params: dict[str, Any] = {
            'workflow_id': str(workflow_id),
            'offset': offset,
            'limit': limit,
        }
        response = self._get('/v1/workflow-nodes/', params=params)
        return [LogicalNodePublic.model_validate(n) for n in response.json()]

    def list_edges(
        self,
        workflow_id: str | UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[LogicalEdgePublic]:
        """List all edges for a workflow.

        Args:
            workflow_id: UUID of the workflow.
            offset: Pagination offset.
            limit: Maximum number of results.

        Returns:
            List of edge objects.
        """
        params: dict[str, Any] = {
            'workflow_id': str(workflow_id),
            'offset': offset,
            'limit': limit,
        }
        response = self._get('/v1/workflow-edges/', params=params)
        return [LogicalEdgePublic.model_validate(e) for e in response.json()]

    def get_metadata(self, workflow_id: str | UUID) -> WorkflowMetadataPublic:
        """Get metadata for a workflow.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The workflow metadata object.

        Raises:
            NotFoundError: If the workflow does not exist.
        """
        response = self._get(f'/v1/workflow-metadata/{workflow_id}')
        return WorkflowMetadataPublic.model_validate(response.json())

    # ==================================================================
    # V1 Atomic Save (POST /v1/workflows/complete)
    # ==================================================================

    def save_complete_workflow(self, payload: dict[str, Any]) -> SaveCompleteWorkflowResponse:
        """Atomically create or update a complete workflow.

        Sends the full workflow definition (workflow + nodes + I/O + edges +
        visuals + metadata) in a single request. The server wraps all
        operations in a database transaction — if any part fails, the
        entire save is rolled back.

        Args:
            payload: Complete workflow payload matching the
                ``SaveCompleteWorkflowRequest`` schema. Include ``workflow_id``
                for updates, omit it for creates.

        Returns:
            The saved workflow with all generated UUIDs and timestamps.

        Raises:
            ValidationError: If the payload fails server-side validation.
        """
        response = self._post('/v1/workflows/complete', json=payload)
        return SaveCompleteWorkflowResponse.model_validate(response.json())

    # ==================================================================
    # Dependency Resolution
    # ==================================================================

    def find_agent_by_name(self, name: str) -> dict[str, Any] | None:
        """Find an agent by name (case-insensitive).

        Since the API does not support server-side name filtering, this
        method lists all accessible agents and filters client-side.

        Args:
            name: The agent name to search for.

        Returns:
            Agent data as a dict if found, None otherwise.
        """
        response = self._get('/v1/agents/', params={'offset': 0, 'limit': 100})
        agents: list[dict[str, Any]] = response.json()
        name_lower = name.lower()
        for agent in agents:
            if agent.get('name', '').lower() == name_lower:
                return agent
        return None

    def find_knowledge_base_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a knowledge base by name (case-insensitive).

        Since the API does not support server-side name filtering, this
        method lists all accessible knowledge bases and filters client-side.

        Args:
            name: The knowledge base name to search for.

        Returns:
            Knowledge base data as a dict if found, None otherwise.
        """
        response = self._get('/v1/knowledge_bases/', params={'offset': 0, 'limit': 100})
        knowledge_bases: list[dict[str, Any]] = response.json()
        name_lower = name.lower()
        for kb in knowledge_bases:
            if kb.get('name', '').lower() == name_lower:
                return kb
        return None

    # ==================================================================
    # V2 Temporal Execution
    # ==================================================================

    def start_workflow_temporal(
        self,
        workflow_id: str | UUID,
        inputs: dict[str, Any] | None = None,
    ) -> TemporalStartResponse:
        """Start a workflow execution via Temporal (non-streaming).

        Returns immediately with a ``run_id`` that can be used for
        subsequent status checks, input submissions, and reviews.

        Args:
            workflow_id: UUID of the workflow to execute.
            inputs: Optional initial input data, keyed by node ID.

        Returns:
            Start response containing ``workflow_id``, ``run_id``,
            ``temporal_workflow_id``, ``status``, and ``message``.
        """
        body: dict[str, Any] = {
            'state': {'inputs': inputs or {}},
            'messages': [],
            'tools': [],
            'context': [],
            'forwarded_props': [],
        }
        response = self._post(
            f'/v2/workflows/{workflow_id}/start/temporal',
            json=body,
        )
        return TemporalStartResponse.model_validate(response.json())

    def get_workflow_status(
        self,
        workflow_id: str | UUID,
        run_id: str,
    ) -> WorkflowStatusResponse:
        """Get the current status of a running workflow execution.

        Args:
            workflow_id: UUID of the workflow.
            run_id: The run identifier returned by ``start_workflow_temporal``.

        Returns:
            Status response with current execution state.
        """
        response = self._get(
            f'/v2/workflows/{workflow_id}/status',
            params={'run_id': run_id},
        )
        return WorkflowStatusResponse.model_validate(response.json())

    def submit_input(
        self,
        workflow_id: str | UUID,
        *,
        run_id: str,
        node_id: str,
        input_data: dict[str, Any],
    ) -> SubmitInputResponse:
        """Submit input data to a paused INPUT node.

        Args:
            workflow_id: UUID of the workflow.
            run_id: The run identifier.
            node_id: ID of the INPUT node waiting for data.
            input_data: The input data payload.

        Returns:
            Submission confirmation.
        """
        body = {'node_id': node_id, 'input_data': input_data}
        response = self._post(
            f'/v2/workflows/{workflow_id}/input',
            json=body,
            params={'run_id': run_id},
        )
        return SubmitInputResponse.model_validate(response.json())

    def submit_review(
        self,
        workflow_id: str | UUID,
        *,
        run_id: str,
        decision: str,
        feedback: str | None = None,
        reviewer_id: str | None = None,
    ) -> SubmitReviewResponse:
        """Submit a human review decision to a paused HUMAN_REVIEW node.

        Args:
            workflow_id: UUID of the workflow.
            run_id: The run identifier.
            decision: Review decision — ``"approve"``, ``"reject"``, or ``"revise"``.
            feedback: Optional feedback text.
            reviewer_id: Optional reviewer identifier.

        Returns:
            Submission confirmation.
        """
        body: dict[str, Any] = {'decision': decision}
        if feedback is not None:
            body['feedback'] = feedback
        if reviewer_id is not None:
            body['reviewer_id'] = reviewer_id
        response = self._post(
            f'/v2/workflows/{workflow_id}/review',
            json=body,
            params={'run_id': run_id},
        )
        return SubmitReviewResponse.model_validate(response.json())
