"""Shared pytest fixtures and configuration for workflow CLI tests."""

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
