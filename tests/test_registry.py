"""Tests for registry client: fetch, cache, staleness, and offline fallback.

BDD Scenarios:
  - Fetch registry from API endpoint (REG-01)
  - Cache with 24h TTL, load/save/expiry (REG-02)
  - Fallback to stale cache when API unreachable (REG-03)
  - SKIP when no cache and offline (REG-05)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from cli.registry import (
    RegistryCache,
    fetch_registry,
    get_registry,
    load_cache,
    save_cache,
)

SAMPLE_REGISTRY = {
    'version': '1.0',
    'generated_at': '2026-04-15T10:00:00Z',
    'categories': {'llm': {'name': 'LLM', 'node_types': ['llm_generation']}},
    'all_node_types': ['llm_generation', 'input', 'output'],
}

SAMPLE_HOST = 'https://api.example.com'


class TestFetchRegistry:
    """Fetch registry data from the backend API via httpx GET.

    Given a host URL and a running API
    When fetch_registry(host) is called
    Then it sends GET to {host}/v2/workflow-node-types/registry with 10s timeout
    And returns the parsed JSON response.
    """

    def test_sends_get_to_registry_endpoint(self):
        """fetch_registry() calls httpx.get() with correct URL and timeout."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_REGISTRY
        mock_response.raise_for_status = MagicMock()

        with patch('cli.registry.httpx.get', return_value=mock_response) as mock_get:
            result = fetch_registry(SAMPLE_HOST)

        mock_get.assert_called_once_with(
            f'{SAMPLE_HOST}/v2/workflow-node-types/registry',
            timeout=10.0,
        )
        assert result == SAMPLE_REGISTRY

    def test_strips_trailing_slash_from_host(self):
        """fetch_registry() normalizes host with trailing slash before building URL."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_REGISTRY
        mock_response.raise_for_status = MagicMock()

        with patch('cli.registry.httpx.get', return_value=mock_response) as mock_get:
            fetch_registry('https://api.example.com/')

        mock_get.assert_called_once_with(
            'https://api.example.com/v2/workflow-node-types/registry',
            timeout=10.0,
        )

    def test_raises_transport_error_on_network_failure(self):
        """fetch_registry() propagates httpx.TransportError on network failure."""
        with patch('cli.registry.httpx.get', side_effect=httpx.ConnectError('Connection refused')):
            with pytest.raises(httpx.TransportError):
                fetch_registry(SAMPLE_HOST)

    def test_raises_transport_error_on_timeout(self):
        """fetch_registry() propagates httpx.TimeoutException on timeout."""
        with patch(
            'cli.registry.httpx.get',
            side_effect=httpx.TimeoutException('Request timed out'),
        ):
            with pytest.raises(httpx.TransportError):
                fetch_registry(SAMPLE_HOST)

    def test_raises_http_status_error_on_non_2xx(self):
        """fetch_registry() raises httpx.HTTPStatusError on non-2xx response."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Server Error',
            request=MagicMock(),
            response=mock_response,
        )

        with patch('cli.registry.httpx.get', return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                fetch_registry(SAMPLE_HOST)


class TestRegistryCache:
    """RegistryCache model with TTL expiry and host matching.

    Given a RegistryCache with fetched_at, host, ttl_hours, and registry data
    When is_expired() or host_matches() is called
    Then it returns correct staleness/match results.
    """

    def test_is_expired_returns_false_when_fresh(self):
        """is_expired() returns False when cache age is less than ttl_hours."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=1),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        assert cache.is_expired() is False

    def test_is_expired_returns_true_when_stale(self):
        """is_expired() returns True when cache age exceeds ttl_hours."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=25),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        assert cache.is_expired() is True

    def test_host_matches_same_host(self):
        """host_matches() returns True for the same host."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        assert cache.host_matches(SAMPLE_HOST) is True

    def test_host_matches_with_trailing_slash(self):
        """host_matches() normalizes trailing slashes before comparing."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host='https://api.example.com',
            registry=SAMPLE_REGISTRY,
        )
        assert cache.host_matches('https://api.example.com/') is True

    def test_host_matches_returns_false_for_different_hosts(self):
        """host_matches() returns False when hosts differ."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        assert cache.host_matches('https://other.example.com') is False

    def test_default_ttl_hours(self):
        """RegistryCache.ttl_hours defaults to 24."""
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        assert cache.ttl_hours == 24


class TestSaveCache:
    """Atomic cache file write.

    Given a RegistryCache object
    When save_cache() is called
    Then it writes a valid JSON file atomically via temp file + os.replace.
    """

    def test_creates_parent_directory_if_missing(self, tmp_path: Path):
        """save_cache() creates ~/.workflow/ directory if it does not exist."""
        cache_path = tmp_path / 'subdir' / 'registry-cache.json'
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(cache, cache_path=cache_path)
        assert cache_path.exists()

    def test_writes_valid_json_loadable_by_load_cache(self, tmp_path: Path):
        """save_cache() writes JSON that load_cache() can round-trip."""
        cache_path = tmp_path / 'registry-cache.json'
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(cache, cache_path=cache_path)
        loaded = load_cache(cache_path=cache_path)
        assert loaded is not None
        assert loaded.host == SAMPLE_HOST
        assert loaded.registry == SAMPLE_REGISTRY

    def test_uses_atomic_write(self, tmp_path: Path):
        """save_cache() writes via temp file + os.replace (file exists after write)."""
        cache_path = tmp_path / 'registry-cache.json'
        cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(cache, cache_path=cache_path)
        # Verify file exists and contains valid JSON
        data = json.loads(cache_path.read_text())
        assert data['host'] == SAMPLE_HOST
        assert data['registry'] == SAMPLE_REGISTRY

    def test_file_contains_all_fields(self, tmp_path: Path):
        """save_cache() writes all RegistryCache fields to disk."""
        cache_path = tmp_path / 'registry-cache.json'
        now = datetime.now(UTC)
        cache = RegistryCache(
            fetched_at=now,
            host=SAMPLE_HOST,
            ttl_hours=12,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(cache, cache_path=cache_path)
        data = json.loads(cache_path.read_text())
        assert 'fetched_at' in data
        assert data['host'] == SAMPLE_HOST
        assert data['ttl_hours'] == 12
        assert data['registry'] == SAMPLE_REGISTRY


class TestLoadCache:
    """Load registry cache from disk.

    Given a cache file on disk (or not)
    When load_cache() is called
    Then it returns RegistryCache or None depending on file state.
    """

    def test_returns_none_when_file_missing(self, tmp_path: Path):
        """load_cache() returns None when cache file does not exist."""
        cache_path = tmp_path / 'nonexistent.json'
        assert load_cache(cache_path=cache_path) is None

    def test_returns_none_when_file_corrupt(self, tmp_path: Path):
        """load_cache() returns None when cache file contains invalid JSON."""
        cache_path = tmp_path / 'registry-cache.json'
        cache_path.write_text('not valid json {{{')
        assert load_cache(cache_path=cache_path) is None

    def test_returns_registry_cache_from_valid_file(self, tmp_path: Path):
        """load_cache() returns RegistryCache with correct fields from valid file."""
        cache_path = tmp_path / 'registry-cache.json'
        now = datetime.now(UTC)
        data = {
            'fetched_at': now.isoformat(),
            'host': SAMPLE_HOST,
            'ttl_hours': 24,
            'registry': SAMPLE_REGISTRY,
        }
        cache_path.write_text(json.dumps(data))
        loaded = load_cache(cache_path=cache_path)
        assert loaded is not None
        assert loaded.host == SAMPLE_HOST
        assert loaded.registry == SAMPLE_REGISTRY
        assert loaded.ttl_hours == 24


class TestGetRegistry:
    """Three-tier degradation cascade for registry access.

    Given varying cache/network conditions
    When get_registry() is called
    Then it returns data from the best available source.
    """

    def test_returns_fresh_cache_without_fetching(self, tmp_path: Path):
        """get_registry() returns fresh cache when TTL valid and host matches."""
        cache_path = tmp_path / 'registry-cache.json'
        cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=1),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(cache, cache_path=cache_path)

        with patch('cli.registry.fetch_registry') as mock_fetch:
            result = get_registry(SAMPLE_HOST, cache_path=cache_path)

        mock_fetch.assert_not_called()
        assert result is not None
        assert result.registry == SAMPLE_REGISTRY
        assert result.is_stale is False

    def test_fetches_and_saves_when_cache_expired(self, tmp_path: Path):
        """get_registry() fetches and saves when cache expired."""
        cache_path = tmp_path / 'registry-cache.json'
        old_cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=25),
            host=SAMPLE_HOST,
            registry={'old': True},
        )
        save_cache(old_cache, cache_path=cache_path)

        new_registry = {'version': '2.0', 'new': True}
        with patch('cli.registry.fetch_registry', return_value=new_registry):
            result = get_registry(SAMPLE_HOST, cache_path=cache_path)

        assert result is not None
        assert result.registry == new_registry
        assert result.is_stale is False
        # Verify cache was updated on disk
        saved = load_cache(cache_path=cache_path)
        assert saved is not None
        assert saved.registry == new_registry

    def test_fetches_and_saves_when_host_mismatch(self, tmp_path: Path):
        """get_registry() fetches when cached host differs from current host."""
        cache_path = tmp_path / 'registry-cache.json'
        old_cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=1),
            host='https://old-host.com',
            registry={'old': True},
        )
        save_cache(old_cache, cache_path=cache_path)

        new_registry = {'version': '2.0', 'new_host': True}
        with patch('cli.registry.fetch_registry', return_value=new_registry):
            result = get_registry(SAMPLE_HOST, cache_path=cache_path)

        assert result is not None
        assert result.registry == new_registry
        assert result.is_stale is False

    def test_returns_stale_cache_when_fetch_fails(self, tmp_path: Path):
        """get_registry() returns stale cache with is_stale=True when fetch fails."""
        cache_path = tmp_path / 'registry-cache.json'
        stale_cache = RegistryCache(
            fetched_at=datetime.now(UTC) - timedelta(hours=25),
            host=SAMPLE_HOST,
            registry=SAMPLE_REGISTRY,
        )
        save_cache(stale_cache, cache_path=cache_path)

        with patch(
            'cli.registry.fetch_registry',
            side_effect=httpx.ConnectError('Connection refused'),
        ):
            result = get_registry(SAMPLE_HOST, cache_path=cache_path)

        assert result is not None
        assert result.registry == SAMPLE_REGISTRY
        assert result.is_stale is True

    def test_returns_none_when_fetch_fails_and_no_cache(self, tmp_path: Path):
        """get_registry() returns None when fetch fails and no cache exists."""
        cache_path = tmp_path / 'nonexistent.json'

        with patch(
            'cli.registry.fetch_registry',
            side_effect=httpx.ConnectError('Connection refused'),
        ):
            result = get_registry(SAMPLE_HOST, cache_path=cache_path)

        assert result is None

    def test_returns_none_when_host_is_none(self, tmp_path: Path):
        """get_registry(host=None) returns None without attempting fetch."""
        cache_path = tmp_path / 'registry-cache.json'

        with patch('cli.registry.fetch_registry') as mock_fetch:
            result = get_registry(None, cache_path=cache_path)

        mock_fetch.assert_not_called()
        assert result is None

    def test_returns_none_when_offline(self, tmp_path: Path):
        """get_registry(offline=True) returns None without attempting fetch."""
        cache_path = tmp_path / 'registry-cache.json'

        with patch('cli.registry.fetch_registry') as mock_fetch:
            result = get_registry(SAMPLE_HOST, cache_path=cache_path, offline=True)

        mock_fetch.assert_not_called()
        assert result is None
