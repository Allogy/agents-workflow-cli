"""Tests for workflow delete command.

BDD Scenarios from RAG-954:
  - Delete workflow by ID
  - Delete workflow by name
  - Force delete skips confirmation
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.fixture
def mock_workflow():
    """Create a single mock workflow for deletion tests."""
    workflow_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()

    # Use SimpleNamespace for test objects with arbitrary attributes
    wf = SimpleNamespace(
        id=workflow_id,
        version=1,
        organization_id=org_id,
        created_by=user_id,
        created_at=datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 2, 18, 14, 30, 0, tzinfo=UTC),
    )

    return wf


@pytest.fixture
def mock_metadata(mock_workflow):
    """Create mock metadata for the test workflow."""
    return SimpleNamespace(
        workflow_id=mock_workflow.id,
        name='Test Workflow',
        description='A test workflow',
        tags=['test'],
        is_active=True,
    )


class TestDeleteWorkflowByID:
    """BDD: Delete workflow by ID.

    Given a workflow exists
    When "workflow delete <uuid>" is run
    Then a confirmation prompt is shown
    And on confirmation, the workflow is deleted
    And success is reported
    """

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_id_with_confirmation(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test delete by UUID with confirmation prompt."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.delete_workflow.return_value = None
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client
        mock_confirm.return_value = True  # User confirms

        workflow_id = str(mock_workflow.id)
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                workflow_id,
            ],
        )

        # Verify success
        assert result.exit_code == 0
        assert 'deleted' in result.output.lower() or 'success' in result.output.lower()

        # Verify confirmation was asked
        mock_confirm.assert_called_once()

        # Verify workflow was fetched and deleted
        mock_client.get_workflow.assert_called_once_with(workflow_id)
        mock_client.delete_workflow.assert_called_once_with(workflow_id)

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_id_cancelled(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test delete cancelled by user."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client
        mock_confirm.return_value = False  # User cancels

        workflow_id = str(mock_workflow.id)
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                workflow_id,
            ],
        )

        # Should exit successfully (cancellation is not an error)
        assert result.exit_code == 0
        assert 'cancelled' in result.output.lower() or 'aborted' in result.output.lower()

        # Verify delete was NOT called
        mock_client.delete_workflow.assert_not_called()

    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_nonexistent_id(self, mock_client_class):
        """Test deleting a workflow that doesn't exist."""
        mock_client = MagicMock()
        mock_client.get_workflow.side_effect = Exception('Workflow not found')
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        fake_id = str(uuid4())
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
                'delete',
                fake_id,
            ],
        )

        assert result.exit_code != 0
        assert 'not found' in result.output.lower() or 'error' in result.output.lower()


class TestDeleteWorkflowByName:
    """BDD: Delete workflow by name.

    Given a workflow named "Test Workflow" exists
    When "workflow delete 'Test Workflow'" is run
    Then the workflow is found by name and deleted after confirmation
    """

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_exact_name(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test delete by exact workflow name."""
        # Setup mocks
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow]
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.delete_workflow.return_value = None
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client
        mock_confirm.return_value = True

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                'Test Workflow',
            ],
        )

        assert result.exit_code == 0
        assert 'deleted' in result.output.lower() or 'success' in result.output.lower()

        # Verify list was called to find by name
        mock_client.list_workflows.assert_called_once()

        # Verify delete was called with correct ID
        mock_client.delete_workflow.assert_called_once_with(str(mock_workflow.id))

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_fuzzy_name(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test delete with fuzzy name matching (case-insensitive)."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow]
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.delete_workflow.return_value = None
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client
        mock_confirm.return_value = True

        # Try with different case
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                'test workflow',
            ],
        )

        assert result.exit_code == 0
        mock_client.delete_workflow.assert_called_once()

    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_name_not_found(self, mock_client_class):
        """Test delete when workflow name doesn't exist."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = []  # No matches
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
                'delete',
                'Nonexistent Workflow',
            ],
        )

        assert result.exit_code != 0
        assert 'not found' in result.output.lower() or 'no workflow' in result.output.lower()

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_delete_by_name_multiple_matches(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test delete when multiple workflows match the name (ambiguous)."""
        from datetime import UTC, datetime

        # Create two workflows with similar names using SimpleNamespace
        workflow2 = SimpleNamespace(
            id=uuid4(),
            version=1,
            organization_id=mock_workflow.organization_id,
            created_by=mock_workflow.created_by,
            created_at=datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 2, 18, 14, 30, 0, tzinfo=UTC),
        )

        metadata2 = SimpleNamespace(
            workflow_id=workflow2.id,
            name='Test Workflow 2',
            description='Another test workflow',
            tags=['test'],
            is_active=True,
        )

        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow, workflow2]

        # Return different metadata based on workflow_id
        def get_meta(wf_id):
            if str(wf_id) == str(mock_workflow.id):
                return mock_metadata
            else:
                return metadata2

        mock_client.get_metadata.side_effect = get_meta
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client
        mock_confirm.return_value = True

        # Search for partial match
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                'Test Workflow',
            ],
        )

        # Should succeed if it picks the exact match or prompts user
        # Implementation detail: can be error or success depending on strategy
        # For now, expect it handles multiple matches gracefully
        assert 'multiple' in result.output.lower() or result.exit_code == 0


class TestDeleteWorkflowForce:
    """BDD: Force delete skips confirmation.

    Given a workflow exists
    When "workflow delete [uuid] --force" is run
    Then the workflow is deleted without prompting
    """

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_force_flag_skips_confirmation(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test that --force flag bypasses confirmation prompt."""
        mock_client = MagicMock()
        mock_client.get_workflow.return_value = mock_workflow
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.delete_workflow.return_value = None
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        workflow_id = str(mock_workflow.id)
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                workflow_id,
                '--force',
            ],
        )

        assert result.exit_code == 0
        assert 'deleted' in result.output.lower() or 'success' in result.output.lower()

        # Verify confirmation was NOT asked
        mock_confirm.assert_not_called()

        # Verify delete was called
        mock_client.delete_workflow.assert_called_once_with(workflow_id)

    @patch('cli.commands.delete.Confirm.ask')
    @patch('cli.commands.delete.WorkflowClient')
    def test_force_delete_by_name(
        self, mock_client_class, mock_confirm, mock_workflow, mock_metadata
    ):
        """Test force delete with workflow name."""
        mock_client = MagicMock()
        mock_client.list_workflows.return_value = [mock_workflow]
        mock_client.get_metadata.return_value = mock_metadata
        mock_client.delete_workflow.return_value = None
        mock_client_class.from_config.return_value.__enter__.return_value = mock_client

        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(mock_workflow.organization_id),
                'delete',
                'Test Workflow',
                '--force',
            ],
        )

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        mock_client.delete_workflow.assert_called_once()


class TestDeleteErrorHandling:
    """Test error handling for delete command."""

    def test_delete_missing_argument(self):
        """Test that delete requires a workflow identifier."""
        result = runner.invoke(
            app,
            [
                '--host',
                'https://api.example.com',
                '--api-key',
                'test-key',
                '--org',
                str(uuid4()),
                'delete',
            ],
        )

        assert result.exit_code != 0
        # Should show usage or error about missing argument

    def test_delete_missing_config(self):
        """Test that missing configuration shows clear error."""
        result = runner.invoke(app, ['delete', str(uuid4())])

        assert result.exit_code != 0
        assert 'host' in result.output.lower() or 'config' in result.output.lower()
