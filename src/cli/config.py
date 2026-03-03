"""Configuration loading for the Workflow CLI.

Supports three layers of configuration with precedence:
  CLI flags > environment variables > config file > defaults

Environment variables:
  WORKFLOW_API_HOST  — API host URL
  WORKFLOW_API_KEY   — API authentication key
  WORKFLOW_ORG_ID    — Organization ID

Config file:
  ~/.workflow/config.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path.home() / '.workflow' / 'config.yaml'

# Environment variable names
ENV_HOST = 'WORKFLOW_API_HOST'
ENV_API_KEY = 'WORKFLOW_API_KEY'
ENV_ORG_ID = 'WORKFLOW_ORG_ID'
ENV_RUN_TIMEOUT = 'WORKFLOW_RUN_TIMEOUT'

# Defaults
DEFAULT_RUN_TIMEOUT_SECONDS = 1800  # 30 minutes


class OutputFormat(str, Enum):
    """Supported output formats."""

    JSON = 'json'
    YAML = 'yaml'


class ConfigError(Exception):
    """Raised when configuration is invalid or incomplete."""


@dataclass
class CLIConfig:
    """Resolved CLI configuration.

    All fields are optional at construction time so partial configs
    (e.g. from a config file alone) can be represented. Call
    ``validate_for_api()`` before making API calls.
    """

    host: str | None = None
    api_key: str | None = None
    org_id: str | None = None
    output_format: OutputFormat | None = None

    def validate_for_api(self) -> None:
        """Raise ConfigError if any required API credential is missing."""
        missing: list[str] = []
        if not self.host:
            missing.append('host (--host or WORKFLOW_API_HOST)')
        if not self.api_key:
            missing.append('api-key (--api-key or WORKFLOW_API_KEY)')
        if not self.org_id:
            missing.append('org (--org or WORKFLOW_ORG_ID)')

        if missing:
            hint = (
                'Configure credentials via CLI flags, environment variables, '
                f'or {DEFAULT_CONFIG_PATH}'
            )
            raise ConfigError(f'Missing required configuration: {", ".join(missing)}.\n{hint}')


def _load_config_file(config_path: Path) -> dict:
    """Load and parse a YAML config file, returning an empty dict on missing file."""
    if not config_path.is_file():
        return {}

    try:
        text = config_path.read_text()
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as exc:
        raise ConfigError(f'Invalid config file at {config_path}: {exc}') from exc


def _get_env(name: str) -> str | None:
    """Return an environment variable value, treating empty strings as unset."""
    value = os.environ.get(name, '').strip()
    return value if value else None


def load_config(config_path: Path | None = None) -> CLIConfig:
    """Load configuration from config file and environment variables.

    Precedence (within this function): env vars > config file > defaults.
    CLI flag overrides are applied separately via ``resolve_config()``.
    """
    path = config_path if config_path is not None else DEFAULT_CONFIG_PATH
    file_data = _load_config_file(path)

    # Start with config file values
    host = file_data.get('host')
    api_key = file_data.get('api_key')
    org_id = file_data.get('org_id')
    fmt = file_data.get('format')

    # Environment variables override config file
    env_host = _get_env(ENV_HOST)
    env_api_key = _get_env(ENV_API_KEY)
    env_org_id = _get_env(ENV_ORG_ID)

    if env_host is not None:
        host = env_host
    if env_api_key is not None:
        api_key = env_api_key
    if env_org_id is not None:
        org_id = env_org_id

    # Resolve output format (None means "not explicitly set")
    output_format: OutputFormat | None = None
    if fmt and isinstance(fmt, str):
        try:
            output_format = OutputFormat(fmt.lower())
        except ValueError:
            pass  # Keep as None

    return CLIConfig(
        host=host if host else None,
        api_key=api_key if api_key else None,
        org_id=org_id if org_id else None,
        output_format=output_format,
    )


def resolve_config(
    base: CLIConfig,
    *,
    host: str | None = None,
    api_key: str | None = None,
    org_id: str | None = None,
    output_format: str | None = None,
) -> CLIConfig:
    """Apply CLI flag overrides on top of the base (env + file) config.

    Only non-None flag values override the base config.
    """
    resolved_format = base.output_format
    if output_format is not None:
        try:
            resolved_format = OutputFormat(output_format.lower())
        except ValueError:
            pass  # Keep base value

    return CLIConfig(
        host=host if host is not None else base.host,
        api_key=api_key if api_key is not None else base.api_key,
        org_id=org_id if org_id is not None else base.org_id,
        output_format=resolved_format,
    )


def get_run_timeout(cli_flag: int | None = None) -> int:
    """Resolve the run command timeout in seconds.

    Precedence: CLI flag > env var (WORKFLOW_RUN_TIMEOUT) > default (1800s).

    Args:
        cli_flag: Timeout value from --timeout CLI flag, or None.

    Returns:
        Timeout in seconds.
    """
    if cli_flag is not None:
        return cli_flag

    env_val = _get_env(ENV_RUN_TIMEOUT)
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass  # Fall through to default

    return DEFAULT_RUN_TIMEOUT_SECONDS
