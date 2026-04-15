"""Unit tests for registry CLI commands (refresh, status).

Tests the Typer subcommand group and its integration with the
registry client module from Plan 01.

Reference: Phase 41, Plan 02
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from cli.config import CLIConfig
from cli.registry import RegistryCache

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
