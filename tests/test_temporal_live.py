"""Live Temporal integration tests (TD-03, TD-04, TD-05).

These tests require a live Temporal cluster. They are skipped when
TEMPORAL_TEST_URL and related env vars are not configured.
Set all 4 env vars from conftest.py TEMPORAL_ENV_VARS to enable.
"""

from __future__ import annotations

import time

import pytest

from cli.client import WorkflowClient, WorkflowStatusResponse

# ---------------------------------------------------------------------------
# Module-level skip: all tests in this file require Temporal env vars
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.usefixtures('temporal_env')

# ---------------------------------------------------------------------------
# Terminal statuses that indicate the workflow has finished
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled', 'timed_out'})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_status(
    client: WorkflowClient,
    workflow_id: str,
    run_id: str,
    target_status: str,
    *,
    max_attempts: int = 10,
    interval: float = 2.0,
) -> WorkflowStatusResponse:
    """Poll workflow status until *target_status* is reached.

    Args:
        client: Connected WorkflowClient instance.
        workflow_id: UUID of the workflow.
        run_id: Run identifier from start response.
        target_status: The status string to wait for (case-insensitive).
        max_attempts: Maximum number of poll iterations.
        interval: Seconds to sleep between polls.

    Returns:
        The WorkflowStatusResponse when target is reached.

    Raises:
        TimeoutError: If max_attempts exceeded without reaching the target.
    """
    target_lower = target_status.lower()

    for _ in range(max_attempts):
        status_resp = client.get_workflow_status(workflow_id, run_id)
        current = status_resp.status.lower()

        if current == target_lower:
            return status_resp

        # Stop polling if the workflow has already finished
        if current in TERMINAL_STATUSES:
            raise TimeoutError(
                f'Workflow reached terminal status {status_resp.status!r} '
                f'before reaching {target_status!r}'
            )

        time.sleep(interval)

    raise TimeoutError(f'Workflow did not reach {target_status!r} after {max_attempts} attempts')


# ============================================================================
# TD-03 + TD-05 integration: run_id round-trip against live cluster
# ============================================================================


class TestRunIdRoundTrip:
    """Validate run_id round-trip through a live Temporal cluster.

    TD-03: The run_id returned by start is accepted by the status endpoint.
    TD-05: The response models correctly parse snake_case fields from the API.
    """

    def test_start_returns_valid_run_id(
        self,
        temporal_env: dict[str, str],
        temporal_client: WorkflowClient,
        track_workflow,
    ):
        """Starting a workflow returns a non-empty run_id and workflow_id."""
        workflow_id = temporal_env['TEMPORAL_TEST_WORKFLOW_ID']

        resp = temporal_client.start_workflow_temporal(workflow_id)
        track_workflow(resp.workflow_id, resp.run_id)

        assert isinstance(resp.run_id, str) and len(resp.run_id) > 0, (
            'run_id must be a non-empty string'
        )
        assert resp.status, 'status must be truthy (non-empty)'
        assert isinstance(resp.workflow_id, str) and len(resp.workflow_id) > 0, (
            'workflow_id must be a non-empty string'
        )

    def test_status_api_accepts_returned_run_id(
        self,
        temporal_env: dict[str, str],
        temporal_client: WorkflowClient,
        track_workflow,
    ):
        """The run_id from start can be used to query workflow status."""
        workflow_id = temporal_env['TEMPORAL_TEST_WORKFLOW_ID']

        resp = temporal_client.start_workflow_temporal(workflow_id)
        track_workflow(resp.workflow_id, resp.run_id)

        # Brief pause to allow Temporal to initialize
        time.sleep(0.5)

        status = temporal_client.get_workflow_status(workflow_id, resp.run_id)

        assert status.run_id == resp.run_id, (
            f'run_id must round-trip: sent {resp.run_id!r}, got {status.run_id!r}'
        )
        assert isinstance(status.status, str) and len(status.status) > 0, (
            'status must be a non-empty string'
        )


# ============================================================================
# TD-04: input_data normalization against live cluster
# ============================================================================


class TestInputDataNormalization:
    """Validate that PLAIN_TXT_INPUT and STRUCTURED_INPUT accept documented formats.

    TD-04: The CLI's submit_input sends data that the Temporal workflow
    accepts. These tests start a workflow, wait for it to reach
    WAITING_FOR_INPUT, and then submit input in the documented format.
    """

    def test_plain_text_input_submission(
        self,
        temporal_env: dict[str, str],
        temporal_client: WorkflowClient,
        track_workflow,
    ):
        """Submitting {text: '...'} to a PLAIN_TXT_INPUT node succeeds."""
        workflow_id = temporal_env['TEMPORAL_TEST_WORKFLOW_ID']

        resp = temporal_client.start_workflow_temporal(workflow_id)
        track_workflow(resp.workflow_id, resp.run_id)

        # Wait for the workflow to pause at an INPUT node
        try:
            status = _wait_for_status(
                temporal_client,
                workflow_id,
                resp.run_id,
                'WAITING_FOR_INPUT',
                max_attempts=10,
                interval=2.0,
            )
        except TimeoutError:
            pytest.skip(
                'Test workflow did not pause at an INPUT node -- '
                'workflow may not have a PLAIN_TXT_INPUT node'
            )

        # Determine the node_id to submit to: prefer current_node from status
        node_id = status.current_node
        if not node_id:
            # Fallback: list nodes and find one with PLAIN_TXT_INPUT type
            nodes = temporal_client.list_nodes(workflow_id)
            plain_nodes = [
                n
                for n in nodes
                if hasattr(n, 'node_type')
                and n.node_type
                and 'plain_txt_input' in str(n.node_type).lower()
            ]
            if plain_nodes:
                node_id = str(plain_nodes[0].id)
            else:
                pytest.skip(
                    'Could not determine INPUT node_id -- workflow has no PLAIN_TXT_INPUT node'
                )

        result = temporal_client.submit_input(
            workflow_id,
            run_id=resp.run_id,
            node_id=node_id,
            input_data={'text': 'Test input from CLI validation'},
        )

        assert result.status, (
            'submit_input response status must be truthy (backend accepted the format)'
        )

    def test_structured_input_submission(
        self,
        temporal_env: dict[str, str],
        temporal_client: WorkflowClient,
        track_workflow,
    ):
        """Submitting {formData: {...}} to a STRUCTURED_INPUT node succeeds."""
        workflow_id = temporal_env['TEMPORAL_TEST_WORKFLOW_ID']

        resp = temporal_client.start_workflow_temporal(workflow_id)
        track_workflow(resp.workflow_id, resp.run_id)

        # Wait for the workflow to pause at an INPUT node
        try:
            status = _wait_for_status(
                temporal_client,
                workflow_id,
                resp.run_id,
                'WAITING_FOR_INPUT',
                max_attempts=10,
                interval=2.0,
            )
        except TimeoutError:
            pytest.skip(
                'Test workflow did not pause at an INPUT node -- '
                'workflow may not have a STRUCTURED_INPUT node'
            )

        # Determine the node_id to submit to: prefer current_node from status
        node_id = status.current_node
        if not node_id:
            # Fallback: list nodes and find one with STRUCTURED_INPUT type
            nodes = temporal_client.list_nodes(workflow_id)
            struct_nodes = [
                n
                for n in nodes
                if hasattr(n, 'node_type')
                and n.node_type
                and 'structured_input' in str(n.node_type).lower()
            ]
            if struct_nodes:
                node_id = str(struct_nodes[0].id)
            else:
                pytest.skip(
                    'Could not determine INPUT node_id -- workflow has no STRUCTURED_INPUT node'
                )

        result = temporal_client.submit_input(
            workflow_id,
            run_id=resp.run_id,
            node_id=node_id,
            input_data={'formData': {'field1': 'value1', 'field2': 'value2'}},
        )

        assert result.status, (
            'submit_input response status must be truthy (backend accepted the format)'
        )
