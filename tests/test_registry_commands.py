"""Unit tests for registry CLI commands (refresh, status) and validate registry integration.

Tests the Typer subcommand group, its integration with the
registry client module from Plan 01, and validate command wiring
for auto-fetch, stale warnings, --offline flag, and SKIP rendering.

Reference: Phase 41, Plan 02
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from cli.config import CLIConfig
from cli.registry import RegistryCache, RegistryResult
from cli.validation.runner import CheckResult, CheckStatus

SAMPLE_REGISTRY = {
    'version': '1.0',
    'generated_at': '2026-04-15T10:00:00Z',
    'categories': {},
    'all_node_types': [
        {
            'type': 'llm_call',
            'name': 'LLM Call',
            'status': 'active',
            'fields': [],
            'config_json_schema': {},
        },
        {
            'type': 'agent',
            'name': 'Agent',
            'status': 'active',
            'fields': [],
            'config_json_schema': {},
        },
        {
            'type': 'legacy_type',
            'name': 'Legacy',
            'status': 'inactive',
            'fields': [],
            'config_json_schema': {},
        },
    ],
    'schema_definitions': {},
}


def _make_config(host: str | None = 'https://api.example.com') -> CLIConfig:
    return CLIConfig(host=host)


class TestRefreshCommand:
    """
    Scenario: Registry refresh command fetches and caches registry data
    Given a configured host
    When 'workflow registry refresh' is run
    Then the registry is fetched, cached, and a confirmation is printed
    """

    @patch('cli.commands.registry.save_cache')
    @patch('cli.commands.registry.fetch_registry', return_value=SAMPLE_REGISTRY)
    @patch('cli.main.get_config', return_value=_make_config())
    def test_refresh_success(self, _mock_config, _mock_fetch, _mock_save, cli_invoke):
        """Successful refresh fetches registry, saves cache, and prints confirmation."""
        result = cli_invoke('registry', 'refresh')
        assert result.exit_code == 0
        assert 'Registry updated' in result.stdout
        assert '3' in result.stdout  # 3 node types
        assert '1.0' in result.stdout  # version

    @patch('cli.main.get_config', return_value=_make_config(host=None))
    def test_refresh_no_host(self, _mock_config, cli_invoke):
        """Refresh with no host configured prints error and exits 1."""
        result = cli_invoke('registry', 'refresh')
        assert result.exit_code == 1
        assert 'No host configured' in result.stdout

    @patch(
        'cli.commands.registry.fetch_registry',
        side_effect=httpx.ConnectError('connection refused'),
    )
    @patch('cli.main.get_config', return_value=_make_config())
    def test_refresh_network_error(self, _mock_config, _mock_fetch, cli_invoke):
        """Network error prints clear message and exits 1."""
        result = cli_invoke('registry', 'refresh')
        assert result.exit_code == 1
        assert 'Could not reach' in result.stdout

    @patch(
        'cli.commands.registry.fetch_registry',
        side_effect=httpx.HTTPStatusError(
            'Server Error',
            request=httpx.Request('GET', 'https://api.example.com/v2/workflow-node-types/registry'),
            response=httpx.Response(500),
        ),
    )
    @patch('cli.main.get_config', return_value=_make_config())
    def test_refresh_http_error(self, _mock_config, _mock_fetch, cli_invoke):
        """HTTP error prints status code and exits 1."""
        result = cli_invoke('registry', 'refresh')
        assert result.exit_code == 1
        assert '500' in result.stdout


class TestStatusCommand:
    """
    Scenario: Registry status command shows cache metadata
    Given a registry cache (or none)
    When 'workflow registry status' is run
    Then cache status information is displayed
    """

    @patch('cli.commands.registry.load_cache')
    def test_status_with_valid_cache(self, mock_load, cli_invoke):
        """Status with valid cache shows 'valid' and cache details."""
        mock_load.return_value = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=2),
            host='https://api.example.com',
            ttl_hours=24,
            registry=SAMPLE_REGISTRY,
        )
        result = cli_invoke('registry', 'status')
        assert result.exit_code == 0
        assert 'valid' in result.stdout
        assert '3 total' in result.stdout
        assert '2 active' in result.stdout
        assert '1 inactive' in result.stdout

    @patch('cli.commands.registry.load_cache')
    def test_status_with_expired_cache(self, mock_load, cli_invoke):
        """Status with expired cache shows 'expired'."""
        mock_load.return_value = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=48),
            host='https://api.example.com',
            ttl_hours=24,
            registry=SAMPLE_REGISTRY,
        )
        result = cli_invoke('registry', 'status')
        assert result.exit_code == 0
        assert 'expired' in result.stdout

    @patch('cli.commands.registry.load_cache', return_value=None)
    def test_status_no_cache(self, _mock_load, cli_invoke):
        """Status with no cache prints helpful message."""
        result = cli_invoke('registry', 'status')
        assert result.exit_code == 0
        assert 'No registry cache found' in result.stdout


# ---------------------------------------------------------------------------
# Fixtures for validate integration tests
# ---------------------------------------------------------------------------

VALID_WORKFLOW_YAML = """\
name: Valid
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
edges: []
entry: a
exit: a
"""


@pytest.fixture()
def valid_workflow_file(tmp_path: Path) -> Path:
    """Create a minimal valid workflow file for testing."""
    wf_file = tmp_path / 'test.workflow.yaml'
    wf_file.write_text(VALID_WORKFLOW_YAML)
    return wf_file


# ---------------------------------------------------------------------------
# Validate + registry integration tests
# ---------------------------------------------------------------------------


class TestValidateRegistryIntegration:
    """
    Scenario: Validate command integrates with registry auto-fetch
    Given a workflow file and registry configuration
    When 'workflow validate' is run
    Then get_registry() is called for auto-fetch/cache behavior
    """

    @patch(
        'cli.commands.validate.get_registry',
        return_value=RegistryResult(registry=SAMPLE_REGISTRY, is_stale=False),
    )
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_auto_fetches_on_first_run(
        self, _mock_config, mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """Validate calls get_registry() on every run for auto-fetch behavior."""
        cli_invoke('validate', str(valid_workflow_file))
        mock_get_registry.assert_called_once_with('https://api.example.com', offline=False)

    @patch(
        'cli.commands.validate.get_registry',
        return_value=RegistryResult(registry=SAMPLE_REGISTRY, is_stale=False),
    )
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_auto_fetch_silent_on_success(
        self, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """Fresh cache hit is silent -- no warning in output."""
        result = cli_invoke('validate', str(valid_workflow_file))
        assert 'Warning' not in result.stdout
        assert 'expired' not in result.stdout

    @patch(
        'cli.commands.validate.get_registry',
        return_value=RegistryResult(registry=SAMPLE_REGISTRY, is_stale=True),
    )
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_stale_cache_shows_warning(
        self, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """Stale cache shows warning mentioning 'workflow registry refresh'."""
        result = cli_invoke('validate', str(valid_workflow_file))
        assert 'Registry cache expired' in result.stdout
        assert 'workflow registry refresh' in result.stdout

    @patch('cli.commands.validate.get_registry', return_value=None)
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_no_registry_no_warning(
        self, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """No registry result (None) produces no warning."""
        result = cli_invoke('validate', str(valid_workflow_file))
        assert 'Warning' not in result.stdout


class TestValidateOfflineFlag:
    """
    Scenario: Validate --offline flag skips registry checks
    Given the --offline flag
    When 'workflow validate --offline' is run
    Then offline=True is passed to get_registry()
    """

    @patch('cli.commands.validate.get_registry', return_value=None)
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_offline_flag_accepted(
        self, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """The --offline flag is accepted without error."""
        result = cli_invoke('validate', '--offline', str(valid_workflow_file))
        assert result.exit_code == 0

    @patch('cli.commands.validate.get_registry', return_value=None)
    @patch('cli.main.get_config', return_value=_make_config())
    def test_validate_offline_passes_to_get_registry(
        self, _mock_config, mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """The --offline flag passes offline=True to get_registry()."""
        cli_invoke('validate', '--offline', str(valid_workflow_file))
        mock_get_registry.assert_called_once_with('https://api.example.com', offline=True)

    @patch('cli.commands.validate.get_registry', return_value=None)
    @patch('cli.main.get_config', return_value=_make_config())
    @patch('cli.commands.validate.run_all_validations')
    def test_validate_skip_rendering(
        self, mock_run, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """SKIP status renders as dim text in the table."""
        mock_run.return_value = [
            CheckResult(check_name='YAML Syntax', status=CheckStatus.PASS),
            CheckResult(
                check_name='Registry Check',
                status=CheckStatus.SKIP,
                message='No registry data',
            ),
        ]
        result = cli_invoke('validate', str(valid_workflow_file))
        assert 'SKIP' in result.stdout

    @patch('cli.commands.validate.get_registry', return_value=None)
    @patch('cli.main.get_config', return_value=_make_config())
    @patch('cli.commands.validate.run_all_validations')
    def test_validate_summary_includes_skip_count(
        self, mock_run, _mock_config, _mock_get_registry, cli_invoke, valid_workflow_file
    ):
        """Summary line includes skip count when SKIP results are present."""
        mock_run.return_value = [
            CheckResult(check_name='YAML Syntax', status=CheckStatus.PASS),
            CheckResult(check_name='Schema', status=CheckStatus.PASS),
            CheckResult(
                check_name='Registry Check',
                status=CheckStatus.SKIP,
                message='No registry data',
            ),
        ]
        result = cli_invoke('validate', str(valid_workflow_file))
        assert 'skipped' in result.stdout
