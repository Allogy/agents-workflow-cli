"""Registry client for fetching, caching, and resolving node type registry data.

Provides the data layer for registry-augmented CLI validation:
  - fetch_registry(): HTTP GET to /v2/workflow-node-types/registry
  - RegistryCache: Pydantic model for cache envelope with TTL and host tracking
  - save_cache() / load_cache(): Atomic file I/O for ~/.workflow/registry-cache.json
  - get_registry(): Three-tier degradation cascade (fresh cache, fetch+update, stale fallback)

The registry endpoint is public (no auth required), so this module uses standalone
httpx.get() rather than WorkflowClient.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

CACHE_PATH = Path.home() / '.workflow' / 'registry-cache.json'


class RegistryCache(BaseModel):
    """Metadata envelope for the cached registry response.

    Attributes:
        fetched_at: UTC timestamp of last successful fetch.
        host: API host the registry was fetched from.
        ttl_hours: Cache TTL in hours (default 24).
        registry: Raw registry API response dict.
    """

    fetched_at: datetime
    host: str
    ttl_hours: int = Field(default=24)
    registry: dict

    def is_expired(self) -> bool:
        """Check if the cache TTL has elapsed."""
        age = datetime.now(UTC) - self.fetched_at
        return age.total_seconds() > self.ttl_hours * 3600

    def host_matches(self, current_host: str) -> bool:
        """Check if the cache was fetched from the same host.

        Normalizes trailing slashes before comparing.
        """
        return self.host.rstrip('/') == current_host.rstrip('/')


@dataclass
class RegistryResult:
    """Return type for get_registry() with staleness indicator.

    Attributes:
        registry: The registry data dict.
        is_stale: True if data came from an expired cache (fetch failed).
    """

    registry: dict
    is_stale: bool = False


def fetch_registry(host: str, timeout: float = 10.0) -> dict:
    """Fetch the node type registry from the backend API.

    Args:
        host: Base URL of the platform API.
        timeout: Request timeout in seconds.

    Returns:
        Raw registry response dict.

    Raises:
        httpx.TransportError: Network unreachable, DNS failure, or timeout.
        httpx.HTTPStatusError: Non-2xx response from the API.
    """
    url = f'{host.rstrip("/")}/v2/workflow-node-types/registry'
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def save_cache(cache: RegistryCache, cache_path: Path | None = None) -> None:
    """Atomically write the registry cache to disk.

    Uses tempfile + os.replace() for crash-safe writes.

    Args:
        cache: The RegistryCache to persist.
        cache_path: Override for the cache file location (default: CACHE_PATH).
    """
    if cache_path is None:
        cache_path = CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    data = cache.model_dump(mode='json')
    fd, tmp_path = tempfile.mkstemp(
        dir=str(cache_path.parent),
        suffix='.tmp',
    )
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, str(cache_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_cache(cache_path: Path | None = None) -> RegistryCache | None:
    """Load the registry cache from disk.

    Args:
        cache_path: Override for the cache file location (default: CACHE_PATH).

    Returns:
        RegistryCache if the file exists and is valid, None otherwise.
    """
    if cache_path is None:
        cache_path = CACHE_PATH
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
        return RegistryCache.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


def get_registry(
    host: str | None,
    *,
    cache_path: Path | None = None,
    offline: bool = False,
) -> RegistryResult | None:
    """Get registry data with three-tier degradation cascade.

    1. Fresh cache (TTL valid, host matches) -- use silently
    2. Fetch from API:
       a. Success -- update cache, return fresh data
       b. Failure + stale cache exists -- return stale data with is_stale=True
       c. Failure + no cache -- return None
    3. No host configured or --offline flag -- return None

    Args:
        host: API host URL, or None if not configured.
        cache_path: Override for the cache file location (default: CACHE_PATH).
        offline: If True, skip fetch entirely and return None.

    Returns:
        RegistryResult with registry data and staleness flag, or None.
    """
    if host is None:
        return None
    if offline:
        return None

    if cache_path is None:
        cache_path = CACHE_PATH

    # Try loading existing cache
    cache = load_cache(cache_path=cache_path)

    # Tier 1: Fresh cache
    if cache is not None and not cache.is_expired() and cache.host_matches(host):
        return RegistryResult(registry=cache.registry, is_stale=False)

    # Tier 2: Fetch from API
    try:
        data = fetch_registry(host)
        new_cache = RegistryCache(
            fetched_at=datetime.now(UTC),
            host=host.rstrip('/'),
            registry=data,
        )
        save_cache(new_cache, cache_path=cache_path)
        return RegistryResult(registry=data, is_stale=False)
    except (httpx.TransportError, httpx.HTTPStatusError):
        # Tier 3: Stale fallback
        if cache is not None:
            return RegistryResult(registry=cache.registry, is_stale=True)
        return None
