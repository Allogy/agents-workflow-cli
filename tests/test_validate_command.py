"""Unit tests for the 'workflow validate' CLI command.

Tests both the validation runner (orchestrator) and the Typer command.
Uses temp files and CliRunner for isolated testing.

Reference: Jira RAG-947
"""

from pathlib import Path

from cli.validation.runner import CheckStatus, check_output_variable_paths, run_all_validations


class TestValidationRunner:
    """
    Scenario: Validation runner orchestrates all checks
    Given a workflow YAML string or file path
    When run_all_validations is called
    Then all 10 checks are executed and results returned
    """

    def test_valid_workflow_passes_all_checks(self, tmp_path: Path):
        """Valid workflow passes all 10 validation checks."""
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
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
"""
        results = run_all_validations(wf_yaml)
        assert len(results) == 10
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

    def test_unreachable_nodes_fail(self):
        """Unreachable nodes generate a failure (matches server-side validation)."""
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
    config:
      schema:
        type: object
        properties: {}
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
        assert reachability_check.status == CheckStatus.FAIL
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


class TestUnsupportedNodeValidation:
    """
    Scenario: Unsupported node types are flagged during validation
    Given a workflow with nodes of unsupported types (e.g. document_extraction)
    When run_all_validations is called
    Then the 'Unsupported Node Types' check returns FAIL with details
    """

    def test_document_extraction_node_fails_validation(self):
        """Workflow with document_extraction node fails unsupported check."""
        wf_yaml = """
name: Doc Extract Workflow
description: Contains unsupported document_extraction node
nodes:
  input:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  extract:
    type: document_extraction
    execution_mode: FLOW
    config: {}
  output:
    type: structured_output
    execution_mode: OUTPUT
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: input
    to: extract
  - from: extract
    to: output
entry: input
exit: output
"""
        results = run_all_validations(wf_yaml)
        unsupported_check = next(r for r in results if r.check_name == 'Unsupported Node Types')
        assert unsupported_check.status == CheckStatus.FAIL
        assert 'extract' in unsupported_check.message
        assert 'document_extraction' in unsupported_check.message

    def test_valid_workflow_passes_unsupported_check(self):
        """Workflow without unsupported nodes passes the unsupported check."""
        wf_yaml = """
name: Valid Workflow
description: No unsupported nodes
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
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
"""
        results = run_all_validations(wf_yaml)
        unsupported_check = next(r for r in results if r.check_name == 'Unsupported Node Types')
        assert unsupported_check.status == CheckStatus.PASS


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

    def test_unreachable_nodes_exits_1(self, cli_invoke, tmp_path: Path):
        """Unreachable nodes cause validation failure (exit code 1)."""
        wf_file = tmp_path / 'unreachable.workflow.yaml'
        wf_file.write_text("""
name: Unreachable
nodes:
  entry:
    type: plain_txt_input
    execution_mode: INPUT
    config: {}
  exit:
    type: structured_output
    execution_mode: OUTPUT
    config:
      schema:
        type: object
        properties: {}
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
        assert result.exit_code == 1
        assert 'FAIL' in result.stdout or 'fail' in result.stdout.lower()

    def test_file_not_found_exits_1(self, cli_invoke):
        """Non-existent file exits with code 1."""
        result = cli_invoke('validate', 'nonexistent.workflow.yaml')
        assert result.exit_code == 1
        assert 'not found' in result.stdout.lower() or 'error' in result.stdout.lower()

    def test_output_includes_all_checks(self, cli_invoke, tmp_path: Path):
        """Output includes all 10 validation checks."""
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


# ---------------------------------------------------------------------------
# Output Variable Validation Tests
# ---------------------------------------------------------------------------

SAMPLE_REGISTRY = {
    'all_node_types': [
        {
            'type': 'LLM_CALL',
            'output_variables': [
                {'path': 'output', 'type': 'object'},
                {'path': 'output.text', 'type': 'string'},
                {'path': 'output.output', 'type': 'string'},
            ],
        },
        {
            'type': 'AGENT',
            'output_variables': [
                {'path': 'output', 'type': 'object'},
                {'path': 'output.response', 'type': 'string'},
                {'path': 'output.output', 'type': 'string'},
            ],
        },
        {
            'type': 'STRUCTURED_INPUT',
            'output_variables': [
                {'path': 'output', 'type': 'object'},
                {'path': 'output.formData', 'type': 'object'},
                {'path': 'output.formData.fieldName', 'type': 'any'},
            ],
        },
        {
            'type': 'PLAIN_TXT_INPUT',
            'output_variables': [
                {'path': 'output', 'type': 'object'},
                {'path': 'output.text', 'type': 'string'},
            ],
        },
    ],
}


# Minimal valid workflow YAML with valid output variable references
VALID_OUTPUT_REF_YAML = """
name: Valid Output Refs
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
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
"""

# Workflow YAML with an invalid output variable path
INVALID_OUTPUT_REF_YAML = """
name: Invalid Output Ref
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
      template: "Process: {{input.output.nonexistent}}"
  output:
    type: structured_output
    execution_mode: OUTPUT
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: input
    to: process
  - from: process
    to: output
entry: input
exit: output
"""

# Workflow YAML referencing a STRUCTURED_INPUT node (dynamic outputs)
STRUCTURED_INPUT_REF_YAML = """
name: Structured Input Ref
nodes:
  form:
    type: structured_input
    execution_mode: INPUT
    config:
      schema:
        type: object
        properties:
          grade_level:
            type: string
  process:
    type: llm_call
    execution_mode: MESSAGES
    config:
      model: test-model
      template: "Grade: {{form.output.grade_level}}"
  output:
    type: structured_output
    execution_mode: OUTPUT
    config:
      schema:
        type: object
        properties: {}
edges:
  - from: form
    to: process
  - from: process
    to: output
entry: form
exit: output
"""

# Workflow YAML referencing a non-existent slug (already caught by Check 8)
UNKNOWN_SLUG_REF_YAML = """
name: Unknown Slug Ref
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
      template: "Process: {{ghost.output.text}}"
edges:
  - from: input
    to: process
entry: input
exit: process
"""


class TestOutputVariableValidation:
    """
    Scenario: Output variable paths are validated against registry data
    Given a workflow with {{slug.output.field}} references
    When check_output_variable_paths or run_all_validations is called
    Then paths are validated against the registry's known output_variables
    """

    def test_skip_when_registry_is_none(self):
        """Output Variable Paths check returns SKIP when registry is None."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(VALID_OUTPUT_REF_YAML)
        result = check_output_variable_paths(workflow, registry=None)
        assert result.status == CheckStatus.SKIP
        assert result.check_name == 'Output Variable Paths'
        assert 'unavailable' in result.message.lower()

    def test_pass_when_all_paths_valid(self):
        """Output Variable Paths check returns PASS for valid output references."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(VALID_OUTPUT_REF_YAML)
        result = check_output_variable_paths(workflow, registry=SAMPLE_REGISTRY)
        assert result.status == CheckStatus.PASS
        assert result.check_name == 'Output Variable Paths'

    def test_fail_when_path_invalid(self):
        """Output Variable Paths check returns FAIL for invalid path with alternatives."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(INVALID_OUTPUT_REF_YAML)
        result = check_output_variable_paths(workflow, registry=SAMPLE_REGISTRY)
        assert result.status == CheckStatus.FAIL
        assert result.check_name == 'Output Variable Paths'
        assert 'nonexistent' in result.message
        assert 'PLAIN_TXT_INPUT' in result.message
        assert 'output.text' in result.message

    def test_structured_input_skipped(self):
        """STRUCTURED_INPUT referenced nodes are skipped (no false positives)."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(STRUCTURED_INPUT_REF_YAML)
        result = check_output_variable_paths(workflow, registry=SAMPLE_REGISTRY)
        # Should NOT fail -- STRUCTURED_INPUT has dynamic outputs
        assert result.status == CheckStatus.PASS

    def test_unknown_slug_skipped(self):
        """References to non-existent slugs are skipped (caught by Check 8)."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(UNKNOWN_SLUG_REF_YAML)
        result = check_output_variable_paths(workflow, registry=SAMPLE_REGISTRY)
        # Should not flag unknown slugs -- that is Check 8's job
        assert result.status == CheckStatus.PASS

    def test_unknown_node_type_skipped(self):
        """Node types not in registry are skipped (not flagged as errors)."""
        from cli.wdf_yaml import load_workflow_yaml

        # Use a registry that is missing PLAIN_TXT_INPUT
        partial_registry = {
            'all_node_types': [
                {
                    'type': 'LLM_CALL',
                    'output_variables': [
                        {'path': 'output', 'type': 'object'},
                        {'path': 'output.text', 'type': 'string'},
                    ],
                },
            ],
        }
        workflow = load_workflow_yaml(VALID_OUTPUT_REF_YAML)
        result = check_output_variable_paths(workflow, registry=partial_registry)
        # PLAIN_TXT_INPUT is referenced but not in partial registry -- should skip, not fail
        assert result.status == CheckStatus.PASS

    def test_case_normalization(self):
        """WDF lowercase type 'llm_call' maps to registry uppercase 'LLM_CALL'."""
        from cli.wdf_yaml import load_workflow_yaml

        workflow = load_workflow_yaml(VALID_OUTPUT_REF_YAML)
        # Verify the WDF node type is lowercase
        process_node = workflow.nodes.get('process')
        assert process_node is not None
        assert process_node.type == 'llm_call'
        # Yet the check still validates against LLM_CALL in the registry
        result = check_output_variable_paths(workflow, registry=SAMPLE_REGISTRY)
        assert result.status == CheckStatus.PASS

    def test_backward_compatible_run_all_validations(self):
        """run_all_validations(yaml_str) still works without registry param."""
        results = run_all_validations(VALID_OUTPUT_REF_YAML)
        # Should include the Output Variable Paths check (as SKIP since no registry)
        check_names = [r.check_name for r in results]
        assert 'Output Variable Paths' in check_names
        output_check = next(r for r in results if r.check_name == 'Output Variable Paths')
        assert output_check.status == CheckStatus.SKIP

    def test_run_all_validations_with_registry(self):
        """run_all_validations with registry includes Output Variable Paths in results."""
        results = run_all_validations(VALID_OUTPUT_REF_YAML, registry=SAMPLE_REGISTRY)
        check_names = [r.check_name for r in results]
        assert 'Output Variable Paths' in check_names
        output_check = next(r for r in results if r.check_name == 'Output Variable Paths')
        assert output_check.status == CheckStatus.PASS
