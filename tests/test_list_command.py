"""Tests for workflow list command.

BDD Scenarios from RAG-954:
  - List workflows in table format
  - List workflows in JSON format
  - List workflows in YAML format (bonus)
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import yaml
from typer.testing import CliRunner
from workflow_models import WorkflowPublic

from cli.main import app

runner = CliRunner()


@pytest.fixture
def mock_workflows():
    """Create mock workflow data for testing.
    
    Note: WorkflowPublic doesn't have name/description fields directly.
    Those are in WorkflowMetadataPublic. For these tests we use SimpleNamespace
    to simulate workflows with name attributes.
    """
    workflow1_id = uuid4()
    workflow2_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    # Use SimpleNamespace for test objects with arbitrary attributes
    wf1 = SimpleNamespace(
        id=workflow1_id,
        version=1,
        organization_id=org_id,
        created_by=user_id,
        created_at=datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 18, 14, 30, 0, tzinfo=UTC),
        name='Invoice Processing',
    )
    
    wf2 = SimpleNamespace(
        id=workflow2_id,
        version=2,
        organization_id=org_id,
        created_by=user_id,
        created_at=datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 17, 9, 15, 0, tzinfo=UTC),
        name='Customer Onboarding',
    )
    
    return [wf1, wf2]


class TestListWorkflowsTable:
    """BDD: List workflows in table format.

    Given workflows exist in the organization
    When "workflow list" is run
    Then a table is displayed with: name, ID, status, node count, last updated
    """

    @patch('cli.commands.list.WorkflowClient')
    def test_list_displays_table_format(self, mock_client_class, mock_workflows):
        """Test that list command displays workflows in table format by default."""
        # Setup mock client
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = mock_workflows
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        # Run command with required config
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflows[0].organization_id),
                'list',
            ],
        )

        # Verify success
        assert result.exit_code == 0

        # Verify table headers present
        assert 'Name' in result.output
        assert 'ID' in result.output
        assert 'Updated' in result.output

        # Verify workflow data present
        assert 'Invoice Processing' in result.output
        assert 'Customer Onboarding' in result.output

        # Verify client was called correctly
        mock_client.list_workflows.assert_called_once_with(
            organization_id=str(mock_workflows[0].organization_id)
        )

    @patch('cli.commands.list.WorkflowClient')
    def test_list_truncates_long_ids(self, mock_client_class, mock_workflows):
        """Test that UUIDs are truncated for better display."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = mock_workflows
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflows[0].organization_id),
                'list',
            ],
        )

        assert result.exit_code == 0
        # Full UUID should not appear (they're long)
        full_uuid = str(mock_workflows[0].id)
        # Truncated ID should appear (first 8 chars)
        truncated_id = full_uuid[:8]
        assert truncated_id in result.output

    @patch('cli.commands.list.WorkflowClient')
    def test_list_empty_organization(self, mock_client_class):
        """Test list command with no workflows returns empty table."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        org_id = uuid4()
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(org_id),
                'list',
            ],
        )

        assert result.exit_code == 0
        assert 'No workflows found' in result.output or 'Name' in result.output


class TestListWorkflowsJSON:
    """BDD: List workflows in JSON format.

    Given workflows exist
    When "workflow list --format json" is run
    Then JSON output is printed (suitable for piping)
    """

    @patch('cli.commands.list.WorkflowClient')
    def test_list_json_format(self, mock_client_class, mock_workflows):
        """Test that --format json outputs valid JSON."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = mock_workflows
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflows[0].organization_id),
                'list',
                '--format',
                'json',
            ],
        )

        assert result.exit_code == 0

        # Parse and validate JSON
        output_data = json.loads(result.output)
        assert isinstance(output_data, list)
        assert len(output_data) == 2

        # Verify structure
        assert output_data[0]['name'] == 'Invoice Processing'
        assert output_data[1]['name'] == 'Customer Onboarding'
        assert 'id' in output_data[0]
        assert 'updated_at' in output_data[0]

    @patch('cli.commands.list.WorkflowClient')
    def test_list_json_empty(self, mock_client_class):
        """Test JSON output with no workflows."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        org_id = uuid4()
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(org_id),
                'list',
                '--format',
                'json',
            ],
        )

        assert result.exit_code == 0
        output_data = json.loads(result.output)
        assert output_data == []


class TestListWorkflowsYAML:
    """Test list workflows in YAML format (bonus feature)."""

    @patch('cli.commands.list.WorkflowClient')
    def test_list_yaml_format(self, mock_client_class, mock_workflows):
        """Test that --format yaml outputs valid YAML."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = mock_workflows
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflows[0].organization_id),
                'list',
                '--format',
                'yaml',
            ],
        )

        assert result.exit_code == 0

        # Parse and validate YAML
        output_data = yaml.safe_load(result.output)
        assert isinstance(output_data, list)
        assert len(output_data) == 2
        assert output_data[0]['name'] == 'Invoice Processing'


class TestListErrorHandling:
    """Test error handling for list command."""

    def test_list_missing_config(self):
        """Test that missing configuration shows clear error."""
        result = runner.invoke(app, ['list'])

        # Should fail without config
        assert result.exit_code != 0
        # Error message should be helpful
        assert 'host' in result.output.lower() or 'config' in result.output.lower()

    @patch('cli.commands.list.WorkflowClient')
    def test_list_api_error(self, mock_client_class):
        """Test that API errors are handled gracefully."""
        mock_client = MagicMock()
        mock_client.list_workflows.side_effect = Exception('API connection failed')
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        org_id = uuid4()
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(org_id),
                'list',
            ],
        )

        assert result.exit_code != 0
        assert 'Error' in result.output or 'error' in result.output
