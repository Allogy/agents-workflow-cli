"""Unit tests for the 'workflow init' CLI command.

Tests template scaffolding: interactive selection, non-interactive creation,
template validation, and output file handling.

Reference: Jira RAG-948
"""

from pathlib import Path

import yaml


class TestInitCommandNonInteractive:
    """
    Scenario: Non-interactive template creation
    Given the developer runs "workflow init --template rag-qa -o my-qa.workflow.yaml"
    When the command completes
    Then my-qa.workflow.yaml is created from the rag-qa template
    And the file passes "workflow validate"
    """

    def test_create_from_template_flag(self, cli_invoke, tmp_path: Path):
        """Non-interactive: --template creates the correct file."""
        output_file = tmp_path / 'my-qa.workflow.yaml'
        result = cli_invoke('init', '--template', 'rag-qa', '-o', str(output_file))
        assert result.exit_code == 0
        assert output_file.exists()

    def test_output_file_is_valid_yaml(self, cli_invoke, tmp_path: Path):
        """Generated file is parseable YAML with expected top-level keys."""
        output_file = tmp_path / 'test.workflow.yaml'
        cli_invoke('init', '--template', 'simple-form', '-o', str(output_file))
        data = yaml.safe_load(output_file.read_text())
        assert 'name' in data
        assert 'nodes' in data
        assert 'edges' in data
        assert 'entry' in data
        assert 'exit' in data

    def test_output_file_passes_validation(self, cli_invoke, tmp_path: Path):
        """Generated file passes 'workflow validate'."""
        output_file = tmp_path / 'validated.workflow.yaml'
        cli_invoke('init', '--template', 'rag-qa', '-o', str(output_file))
        result = cli_invoke('validate', str(output_file))
        assert result.exit_code == 0

    def test_default_output_filename(self, cli_invoke, tmp_path: Path, monkeypatch):
        """Without -o, uses {template-name}.workflow.yaml as default."""
        monkeypatch.chdir(tmp_path)
        result = cli_invoke('init', '--template', 'text-to-agent')
        assert result.exit_code == 0
        expected_file = tmp_path / 'text-to-agent.workflow.yaml'
        assert expected_file.exists()

    def test_refuses_overwrite_existing_file(self, cli_invoke, tmp_path: Path):
        """Refuses to overwrite an existing file without --force."""
        output_file = tmp_path / 'existing.workflow.yaml'
        output_file.write_text('existing content')
        result = cli_invoke('init', '--template', 'blank', '-o', str(output_file))
        assert result.exit_code == 1
        assert 'exists' in result.stdout.lower() or 'overwrite' in result.stdout.lower()
        # Original content preserved
        assert output_file.read_text() == 'existing content'

    def test_force_overwrites_existing_file(self, cli_invoke, tmp_path: Path):
        """--force allows overwriting an existing file."""
        output_file = tmp_path / 'existing.workflow.yaml'
        output_file.write_text('old content')
        result = cli_invoke('init', '--template', 'blank', '-o', str(output_file), '--force')
        assert result.exit_code == 0
        assert output_file.read_text() != 'old content'

    def test_unknown_template_exits_1(self, cli_invoke, tmp_path: Path):
        """Unknown template name exits with error."""
        output_file = tmp_path / 'output.workflow.yaml'
        result = cli_invoke('init', '--template', 'nonexistent', '-o', str(output_file))
        assert result.exit_code == 1
        assert 'unknown' in result.stdout.lower() or 'not found' in result.stdout.lower()

    def test_all_templates_available(self, cli_invoke, tmp_path: Path):
        """All 7 documented templates can be created."""
        templates = [
            'simple-form',
            'text-to-agent',
            'document-analysis',
            'form-with-review',
            'batch-processing',
            'rag-qa',
            'blank',
        ]
        for template_name in templates:
            output_file = tmp_path / f'{template_name}.workflow.yaml'
            result = cli_invoke('init', '--template', template_name, '-o', str(output_file))
            assert result.exit_code == 0, f'Template {template_name} failed: {result.stdout}'
            assert output_file.exists(), f'Template {template_name} file not created'


class TestInitCommandInteractive:
    """
    Scenario: Interactive template selection
    Given the developer runs "workflow init"
    When prompted
    Then a list of available templates is shown
    And selecting one generates the corresponding .workflow.yaml file
    """

    def test_no_template_flag_shows_list(self, cli_invoke, tmp_path: Path, monkeypatch):
        """Without --template, shows available templates and prompts."""
        monkeypatch.chdir(tmp_path)
        # When no --template is given and stdin is not interactive,
        # the command should show the template list in the output
        result = cli_invoke('init')
        stdout = result.stdout.lower()
        # Should mention available templates
        assert 'template' in stdout


class TestInitListTemplates:
    """Test the --list flag to display available templates."""

    def test_list_templates(self, cli_invoke):
        """--list shows all available templates with descriptions."""
        result = cli_invoke('init', '--list')
        assert result.exit_code == 0
        stdout = result.stdout.lower()
        assert 'simple-form' in stdout
        assert 'rag-qa' in stdout
        assert 'blank' in stdout


class TestAllTemplatesValid:
    """
    Scenario: All templates are valid
    Given each built-in template
    When parsed and validated
    Then every template passes all validation checks
    """

    def test_every_template_passes_validate(self, cli_invoke, tmp_path: Path):
        """Every built-in template passes 'workflow validate' with exit code 0."""
        templates = [
            'simple-form',
            'text-to-agent',
            'document-analysis',
            'form-with-review',
            'batch-processing',
            'rag-qa',
            'blank',
        ]
        for template_name in templates:
            output_file = tmp_path / f'{template_name}.workflow.yaml'
            init_result = cli_invoke('init', '--template', template_name, '-o', str(output_file))
            assert init_result.exit_code == 0, (
                f'init failed for {template_name}: {init_result.stdout}'
            )

            validate_result = cli_invoke('validate', str(output_file))
            assert validate_result.exit_code == 0, (
                f'validate failed for {template_name}: {validate_result.stdout}'
            )
