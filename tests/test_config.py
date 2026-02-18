"""Tests for configuration loading: env vars, config file, and precedence.

BDD Scenarios from RAG-944:
  - Configuration via environment variables
  - Configuration via config file
  - Missing configuration shows clear error
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from cli.config import (
    CLIConfig,
    ConfigError,
    OutputFormat,
    load_config,
    resolve_config,
)


class TestOutputFormat:
    """Test the OutputFormat enum."""

    def test_json_format(self):
        assert OutputFormat.JSON == 'json'

    def test_yaml_format(self):
        assert OutputFormat.YAML == 'yaml'

    def test_from_string_json(self):
        assert OutputFormat('json') == OutputFormat.JSON

    def test_from_string_yaml(self):
        assert OutputFormat('yaml') == OutputFormat.YAML


class TestCLIConfig:
    """Test the CLIConfig dataclass."""

    def test_create_with_all_fields(self):
        config = CLIConfig(
            host='https://api.example.com',
            api_key='test-key-123',
            org_id='550e8400-e29b-41d4-a716-446655440000',
            output_format=OutputFormat.JSON,
        )
        assert config.host == 'https://api.example.com'
        assert config.api_key == 'test-key-123'
        assert config.org_id == '550e8400-e29b-41d4-a716-446655440000'
        assert config.output_format == OutputFormat.JSON

    def test_default_output_format(self):
        config = CLIConfig(host='https://api.example.com', api_key='key', org_id='org-123')
        assert config.output_format == OutputFormat.JSON

    def test_optional_fields_default_to_none(self):
        config = CLIConfig()
        assert config.host is None
        assert config.api_key is None
        assert config.org_id is None

    def test_validate_raises_when_host_missing(self):
        config = CLIConfig(api_key='key', org_id='org-123')
        with pytest.raises(ConfigError, match='host'):
            config.validate_for_api()

    def test_validate_raises_when_api_key_missing(self):
        config = CLIConfig(host='https://api.example.com', org_id='org-123')
        with pytest.raises(ConfigError, match='api.key'):
            config.validate_for_api()

    def test_validate_raises_when_org_id_missing(self):
        config = CLIConfig(host='https://api.example.com', api_key='key')
        with pytest.raises(ConfigError, match='org'):
            config.validate_for_api()

    def test_validate_passes_with_all_fields(self):
        config = CLIConfig(
            host='https://api.example.com',
            api_key='key',
            org_id='org-123',
        )
        config.validate_for_api()  # Should not raise


class TestLoadConfigFromEnvVars:
    """BDD: Configuration via environment variables.

    Given WORKFLOW_API_HOST, WORKFLOW_API_KEY, and WORKFLOW_ORG_ID are set
    When any command is run without --host, --api-key, --org flags
    Then the environment variable values are used
    """

    def test_loads_host_from_env(self):
        with patch.dict('os.environ', {'WORKFLOW_API_HOST': 'https://env-host.com'}):
            config = load_config()
            assert config.host == 'https://env-host.com'

    def test_loads_api_key_from_env(self):
        with patch.dict('os.environ', {'WORKFLOW_API_KEY': 'env-api-key'}):
            config = load_config()
            assert config.api_key == 'env-api-key'

    def test_loads_org_id_from_env(self):
        with patch.dict('os.environ', {'WORKFLOW_ORG_ID': 'env-org-id'}):
            config = load_config()
            assert config.org_id == 'env-org-id'

    def test_loads_all_env_vars(self):
        env = {
            'WORKFLOW_API_HOST': 'https://env-host.com',
            'WORKFLOW_API_KEY': 'env-key',
            'WORKFLOW_ORG_ID': 'env-org',
        }
        with patch.dict('os.environ', env, clear=False):
            config = load_config()
            assert config.host == 'https://env-host.com'
            assert config.api_key == 'env-key'
            assert config.org_id == 'env-org'

    def test_empty_env_var_treated_as_unset(self):
        with patch.dict('os.environ', {'WORKFLOW_API_HOST': ''}):
            config = load_config()
            assert config.host is None


class TestLoadConfigFromFile:
    """BDD: Configuration via config file.

    Given a ~/.workflow/config.yaml file exists
    When any command is run without flags or env vars
    Then config file values are used as fallback
    """

    def test_loads_from_config_file(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(
            yaml.dump(
                {
                    'host': 'https://file-host.com',
                    'api_key': 'file-key',
                    'org_id': 'file-org',
                }
            )
        )
        config = load_config(config_path=config_file)
        assert config.host == 'https://file-host.com'
        assert config.api_key == 'file-key'
        assert config.org_id == 'file-org'

    def test_loads_format_from_config_file(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(yaml.dump({'format': 'yaml'}))
        config = load_config(config_path=config_file)
        assert config.output_format == OutputFormat.YAML

    def test_missing_config_file_returns_empty_config(self, tmp_path: Path):
        config_file = tmp_path / 'nonexistent.yaml'
        config = load_config(config_path=config_file)
        assert config.host is None
        assert config.api_key is None
        assert config.org_id is None

    def test_empty_config_file_returns_empty_config(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text('')
        config = load_config(config_path=config_file)
        assert config.host is None

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(': invalid: yaml: [')
        with pytest.raises(ConfigError, match='config file'):
            load_config(config_path=config_file)


class TestConfigPrecedence:
    """Test config precedence: CLI flags > env vars > config file > defaults."""

    def test_env_vars_override_config_file(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(
            yaml.dump(
                {
                    'host': 'https://file-host.com',
                    'api_key': 'file-key',
                    'org_id': 'file-org',
                }
            )
        )
        env = {'WORKFLOW_API_HOST': 'https://env-host.com'}
        with patch.dict('os.environ', env, clear=False):
            config = load_config(config_path=config_file)
            # Env var wins over file
            assert config.host == 'https://env-host.com'
            # File values used as fallback
            assert config.api_key == 'file-key'
            assert config.org_id == 'file-org'

    def test_cli_flags_override_env_vars(self, tmp_path: Path):
        env = {
            'WORKFLOW_API_HOST': 'https://env-host.com',
            'WORKFLOW_API_KEY': 'env-key',
            'WORKFLOW_ORG_ID': 'env-org',
        }
        with patch.dict('os.environ', env, clear=False):
            config = load_config()
            resolved = resolve_config(
                config,
                host='https://flag-host.com',
                api_key=None,
                org_id=None,
                output_format=None,
            )
            # CLI flag wins
            assert resolved.host == 'https://flag-host.com'
            # Env var used as fallback
            assert resolved.api_key == 'env-key'
            assert resolved.org_id == 'env-org'

    def test_cli_flags_override_all(self, tmp_path: Path):
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(
            yaml.dump(
                {
                    'host': 'https://file-host.com',
                    'api_key': 'file-key',
                    'org_id': 'file-org',
                    'format': 'yaml',
                }
            )
        )
        env = {
            'WORKFLOW_API_HOST': 'https://env-host.com',
            'WORKFLOW_API_KEY': 'env-key',
            'WORKFLOW_ORG_ID': 'env-org',
        }
        with patch.dict('os.environ', env, clear=False):
            config = load_config(config_path=config_file)
            resolved = resolve_config(
                config,
                host='https://flag-host.com',
                api_key='flag-key',
                org_id='flag-org',
                output_format='json',
            )
            assert resolved.host == 'https://flag-host.com'
            assert resolved.api_key == 'flag-key'
            assert resolved.org_id == 'flag-org'
            assert resolved.output_format == OutputFormat.JSON

    def test_resolve_config_preserves_base_when_no_overrides(self):
        base = CLIConfig(
            host='https://base-host.com',
            api_key='base-key',
            org_id='base-org',
            output_format=OutputFormat.YAML,
        )
        resolved = resolve_config(
            base,
            host=None,
            api_key=None,
            org_id=None,
            output_format=None,
        )
        assert resolved.host == 'https://base-host.com'
        assert resolved.api_key == 'base-key'
        assert resolved.org_id == 'base-org'
        assert resolved.output_format == OutputFormat.YAML


class TestDefaultConfigPath:
    """Test that the default config path points to ~/.workflow/config.yaml."""

    def test_default_config_path(self):
        from cli.config import DEFAULT_CONFIG_PATH

        assert DEFAULT_CONFIG_PATH == Path.home() / '.workflow' / 'config.yaml'
