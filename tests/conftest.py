"""Shared pytest fixtures and configuration for workflow CLI tests."""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from cli.client import WorkflowClient
from cli.main import app

# ---------------------------------------------------------------------------
# Integration test CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register custom CLI flags for integration tests."""
    group = parser.getgroup('integration', 'Live API integration test options')
    group.addoption('--host', default=None, help='API host URL')
    group.addoption('--api-key', default=None, help='API key')
    group.addoption('--org', default=None, help='Organization UUID')


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner() -> CliRunner:
    """Provide a Typer CLI test runner."""
    return CliRunner()


@pytest.fixture()
def cli_invoke(runner: CliRunner):
    """Provide a helper to invoke the CLI app."""

    def _invoke(*args: str):
        return runner.invoke(app, list(args))

    return _invoke


# ---------------------------------------------------------------------------
# Integration test fixtures (require --host, --api-key, --org flags)
# ---------------------------------------------------------------------------


def _skip_if_missing(config: pytest.Config) -> None:
    """Skip the test if any required integration CLI flag is absent."""
    host = config.getoption('--host')
    api_key = config.getoption('--api-key')
    org = config.getoption('--org')
    if not all([host, api_key, org]):
        pytest.skip('Integration tests require --host, --api-key, and --org flags')


@pytest.fixture(scope='module')
def live_client(request: pytest.FixtureRequest) -> WorkflowClient:
    """Create a real WorkflowClient connected to the live API."""
    _skip_if_missing(request.config)
    client = WorkflowClient(
        host=request.config.getoption('--host'),
        api_key=request.config.getoption('--api-key'),
        org_id=request.config.getoption('--org'),
    )
    yield client
    client.close()


@pytest.fixture(scope='module')
def org_id(request: pytest.FixtureRequest) -> str:
    """Return the org UUID from the CLI flag."""
    _skip_if_missing(request.config)
    return request.config.getoption('--org')


# ---------------------------------------------------------------------------
# Live Temporal test fixtures (require TEMPORAL_TEST_* env vars)
# ---------------------------------------------------------------------------

# Required environment variables for live Temporal tests
TEMPORAL_ENV_VARS: dict[str, str] = {
    'TEMPORAL_TEST_URL': 'API base URL (e.g., https://api.staging.example.com)',
    'TEMPORAL_TEST_API_KEY': 'API key for authentication',
    'TEMPORAL_TEST_ORG_ID': 'Organization UUID',
    'TEMPORAL_TEST_WORKFLOW_ID': 'UUID of a test workflow with INPUT + REVIEW nodes',
}


def _require_temporal_env() -> dict[str, str]:
    """Read and validate all required Temporal test env vars.

    Returns a dict of env var name to value if all are set.
    Calls ``pytest.skip()`` with a descriptive message listing all
    missing variables and their descriptions when any are absent.
    """
    values: dict[str, str] = {}
    missing: list[str] = []

    for var, description in TEMPORAL_ENV_VARS.items():
        value = os.environ.get(var)
        if value:
            values[var] = value
        else:
            missing.append(f'  {var} -- {description}')

    if missing:
        msg = 'Live Temporal tests require these environment variables:\n' + '\n'.join(missing)
        pytest.skip(msg)

    return values


@pytest.fixture(scope='module')
def temporal_env() -> dict[str, str]:
    """Provide validated Temporal test environment variables.

    Skips the entire test module when any required env var is missing,
    printing a helpful message listing all missing variables.
    """
    return _require_temporal_env()


@pytest.fixture(scope='module')
def temporal_client(temporal_env: dict[str, str]) -> WorkflowClient:
    """Create a WorkflowClient connected to the live Temporal test cluster.

    Depends on ``temporal_env`` to ensure all required env vars are set.
    The client is closed during teardown.
    """
    client = WorkflowClient(
        host=temporal_env['TEMPORAL_TEST_URL'],
        api_key=temporal_env['TEMPORAL_TEST_API_KEY'],
        org_id=temporal_env['TEMPORAL_TEST_ORG_ID'],
    )
    yield client
    client.close()


@pytest.fixture()
def track_workflow(
    request: pytest.FixtureRequest,
    temporal_client: WorkflowClient,
) -> Callable[[str, str], None]:
    """Track started workflow runs and warn about zombies on teardown.

    Returns a ``register(workflow_id, run_id)`` callable. After the test
    completes, a finalizer checks each tracked run's status and emits a
    warning for any that are still active (not COMPLETED, FAILED,
    CANCELLED, or TIMED_OUT).
    """
    started_runs: list[tuple[str, str]] = []

    def _register(workflow_id: str, run_id: str) -> None:
        started_runs.append((workflow_id, run_id))

    terminal_statuses = frozenset({'completed', 'failed', 'cancelled', 'timed_out'})

    def _finalizer() -> None:
        for wf_id, rid in started_runs:
            try:
                status_resp = temporal_client.get_workflow_status(wf_id, rid)
                status = status_resp.status.lower()
                if status not in terminal_statuses:
                    warnings.warn(
                        f'Zombie workflow: {wf_id}/{rid} still in {status}. '
                        'Manual cleanup may be needed.',
                        stacklevel=1,
                    )
            except Exception:  # noqa: BLE001
                # Workflow may already be gone or cluster unreachable
                pass

    request.addfinalizer(_finalizer)
    return _register
