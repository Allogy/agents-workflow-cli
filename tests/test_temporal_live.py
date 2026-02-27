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
