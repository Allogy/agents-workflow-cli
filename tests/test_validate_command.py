"""Unit tests for the 'workflow validate' CLI command.

Tests both the validation runner (orchestrator) and the Typer command.
Uses temp files and CliRunner for isolated testing.

Reference: Jira RAG-947
"""

from pathlib import Path

from cli.validation.runner import CheckStatus, run_all_validations


class TestValidationRunner:
    """
    Scenario: Validation runner orchestrates all checks
    Given a workflow YAML string or file path
    When run_all_validations is called
    Then all 9 checks are executed and results returned
    """

    def test_valid_workflow_passes_all_checks(self, tmp_path: Path):
        """Valid workflow passes all 9 validation checks."""
        wf_yaml = """
name: Valid Workflow
description: All checks pass
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  process:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test-model
      template: "Process: {{input.output.text}}"
  output:
    type: structured_output
    execution_mode: OUTPUT
    config: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
"""
        results = run_all_validations(wf_yaml)
        assert len(results) == 9
        assert all(r.status in {CheckStatus.PASS, CheckStatus.WARN} for r in results)

        # Check specific checks
        check_names = {r.check_name for r in results}
        assert 'YAML Syntax' in check_names
        assert 'WDF Schema Conformance' in check_names
        assert 'Graph Reachability' in check_names
        assert 'Cycle Detection' in check_names
        assert 'Variable References' in check_names

    def test_invalid_yaml_syntax_fails(self):
        """Invalid YAML syntax is caught with line number."""
        invalid_yaml = """
name: Broken
nodes:
  - this is not: [valid yaml
"""
        results = run_all_validations(invalid_yaml)
        yaml_check = next(r for r in results if r.check_name == 'YAML Syntax')
        assert yaml_check.status == CheckStatus.FAIL
        assert yaml_check.message is not None
        # Should mention 'line' for context
        assert 'line' in yaml_check.message.lower() or 'parsing' in yaml_check.message.lower()

    def test_schema_validation_failure(self):
        """Schema validation failures are caught."""
        invalid_schema = """
name: Missing Entry
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
edges: []
exit: a
"""  # Missing 'entry' field
        results = run_all_validations(invalid_schema)
        schema_check = next(r for r in results if r.check_name == 'WDF Schema Conformance')
        assert schema_check.status == CheckStatus.FAIL
        assert 'entry' in schema_check.message.lower()

    def test_cycle_detection_fails(self):
        """Cycles are detected and reported."""
        cycle_workflow = """
name: Cycle Workflow
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  b:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
edges:
  - from: a
    to: b
  - from: b
    to: a
entry: a
exit: b
"""
        results = run_all_validations(cycle_workflow)
        cycle_check = next(r for r in results if r.check_name == 'Cycle Detection')
        assert cycle_check.status == CheckStatus.FAIL
        assert 'cycle' in cycle_check.message.lower()

    def test_unreachable_nodes_warning(self):
        """Unreachable nodes generate a warning."""
        unreachable_wf = """
name: Unreachable Node
nodes:
  entry:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  exit:
    type: structured_output
    execution_mode: OUTPUT
    config: {}
  orphan:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
edges:
  - from: entry
    to: exit
entry: entry
exit: exit
"""
        results = run_all_validations(unreachable_wf)
        reachability_check = next(r for r in results if r.check_name == 'Graph Reachability')
        assert reachability_check.status == CheckStatus.WARN
        assert 'orphan' in reachability_check.message.lower()

    def test_invalid_variable_reference_fails(self):
        """Invalid variable references are caught."""
        invalid_ref_wf = """
name: Invalid Ref
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  process:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: "{{nonexistent.output.data}}"
edges:
  - from: input
    to: process
entry: input
exit: process
"""
        results = run_all_validations(invalid_ref_wf)
        var_check = next(r for r in results if r.check_name == 'Variable References')
        assert var_check.status == CheckStatus.FAIL
        assert 'nonexistent' in var_check.message.lower()

    def test_unknown_node_type_fails(self):
        """Unknown node types are caught in schema validation."""
        unknown_type_wf = """
name: Unknown Type
nodes:
  bad:
    type: INVALID_TYPE
    execution_mode: INPUT
    config: {}
edges: []
entry: bad
exit: bad
"""
        results = run_all_validations(unknown_type_wf)
        schema_check = next(r for r in results if r.check_name == 'WDF Schema Conformance')
        assert schema_check.status == CheckStatus.FAIL
        # Should mention the invalid type or validation error
        assert 'INVALID_TYPE' in schema_check.message or 'type' in schema_check.message.lower()

    def test_missing_required_config_field_fails(self):
        """Missing required config fields are caught."""
        missing_field_wf = """
name: Missing Field
nodes:
  bad:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      # Missing required 'template' field
edges: []
entry: bad
exit: bad
"""
        results = run_all_validations(missing_field_wf)
        schema_check = next(r for r in results if r.check_name == 'WDF Schema Conformance')
        assert schema_check.status == CheckStatus.FAIL
        assert 'template' in schema_check.message.lower()


class TestValidateCommand:
    """
    Scenario: CLI command validates workflow files
    Given a .workflow.yaml file path
    When 'workflow validate <file>' is run
    Then validation results are displayed and exit code is correct
    """

    def test_valid_file_exits_0(self, cli_invoke, tmp_path: Path):
        """Valid workflow file exits with code 0."""
        wf_file = tmp_path / 'valid.workflow.yaml'
        wf_file.write_text("""
name: Valid
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
edges: []
entry: a
exit: a
""")
        result = cli_invoke('validate', str(wf_file))
        assert result.exit_code == 0
        assert 'PASS' in result.stdout or 'pass' in result.stdout.lower()

    def test_invalid_file_exits_1(self, cli_invoke, tmp_path: Path):
        """Invalid workflow file exits with code 1."""
        wf_file = tmp_path / 'invalid.workflow.yaml'
        wf_file.write_text("""
name: Cycle
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  b:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
edges:
  - from: a
    to: b
  - from: b
    to: a
entry: a
exit: b
""")
        result = cli_invoke('validate', str(wf_file))
        assert result.exit_code == 1
        assert 'FAIL' in result.stdout or 'fail' in result.stdout.lower()

    def test_warnings_only_exits_0(self, cli_invoke, tmp_path: Path):
        """Warnings without failures exit with code 0."""
        wf_file = tmp_path / 'warnings.workflow.yaml'
        wf_file.write_text("""
name: Warnings
nodes:
  entry:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  exit:
    type: structured_output
    execution_mode: OUTPUT
    config: {}
  orphan:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test
      template: test
edges:
  - from: entry
    to: exit
entry: entry
exit: exit
""")
        result = cli_invoke('validate', str(wf_file))
        assert result.exit_code == 0
        assert 'WARN' in result.stdout or 'warn' in result.stdout.lower()

    def test_file_not_found_exits_1(self, cli_invoke):
        """Non-existent file exits with code 1."""
        result = cli_invoke('validate', 'nonexistent.workflow.yaml')
        assert result.exit_code == 1
        assert 'not found' in result.stdout.lower() or 'error' in result.stdout.lower()

    def test_output_includes_all_checks(self, cli_invoke, tmp_path: Path):
        """Output includes all 9 validation checks."""
        wf_file = tmp_path / 'test.workflow.yaml'
        wf_file.write_text("""
name: Test
nodes:
  a:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
edges: []
entry: a
exit: a
""")
        result = cli_invoke('validate', str(wf_file))
        # Check that key validation checks appear in output
        stdout_lower = result.stdout.lower()
        assert 'yaml' in stdout_lower
        assert 'schema' in stdout_lower
        assert 'reachability' in stdout_lower or 'graph' in stdout_lower
        assert 'cycle' in stdout_lower
        assert 'variable' in stdout_lower
