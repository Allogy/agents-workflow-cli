"""Unit tests for WDF graph validation functions.

Tests reachability analysis, cycle detection, and variable reference validation.
All tests use in-memory WorkflowDefinition objects — no I/O, no API calls.

Reference: Jira RAG-947
"""

from workflow_models.wdf import WorkflowDefinition
from workflow_models.wdf.validation import (
    check_cycles,
    check_reachability,
    check_variable_references,
)


class TestReachabilityAnalysis:
    """
    Scenario: Graph reachability validation
    Given a workflow with nodes and edges
    When reachability is checked from the entry point
    Then all nodes should be reachable or unreachable nodes should be reported
    """

    def test_all_nodes_reachable_linear(self):
        """All nodes reachable in a linear pipeline (A -> B -> C)."""
        wf = WorkflowDefinition(
            name='Linear',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'c'},
            ],
            entry='a',
            exit='c',
        )
        result = check_reachability(wf)
        assert result.passed is True
        assert result.message == 'All nodes are reachable from entry point'
        assert result.details is None

    def test_unreachable_node_disconnected(self):
        """Unreachable node detected when a node is disconnected from entry."""
        wf = WorkflowDefinition(
            name='Disconnected',
            nodes={
                'entry': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'process': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'orphan': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'exit': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'entry', 'to': 'process'},
                {'from': 'process', 'to': 'exit'},
                # 'orphan' has no incoming or outgoing edges
            ],
            entry='entry',
            exit='exit',
        )
        result = check_reachability(wf)
        assert result.passed is False
        assert 'orphan' in result.message
        assert result.details is not None
        assert 'orphan' in result.details['unreachable_nodes']

    def test_unreachable_exit_point(self):
        """Exit point is unreachable (edge goes wrong direction)."""
        wf = WorkflowDefinition(
            name='Exit Unreachable',
            nodes={
                'entry': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'exit': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                # No edge from entry to exit, but Pydantic won't catch this
            ],
            entry='entry',
            exit='exit',
        )
        result = check_reachability(wf)
        assert result.passed is False
        assert 'exit' in result.message

    def test_all_nodes_reachable_branching(self):
        """All nodes reachable in a branching workflow (A -> B, A -> C, B -> D, C -> D)."""
        wf = WorkflowDefinition(
            name='Branching',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'd': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'a', 'to': 'c'},
                {'from': 'b', 'to': 'd'},
                {'from': 'c', 'to': 'd'},
            ],
            entry='a',
            exit='d',
        )
        result = check_reachability(wf)
        assert result.passed is True

    def test_single_node_workflow(self):
        """Single-node workflow is trivially reachable."""
        wf = WorkflowDefinition(
            name='Single Node',
            nodes={
                'only': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
            },
            edges=[],
            entry='only',
            exit='only',
        )
        result = check_reachability(wf)
        assert result.passed is True


class TestCycleDetection:
    """
    Scenario: Cycle detection in workflow graphs
    Given a workflow with edges
    When cycles are checked
    Then circular dependencies should be detected (except RECURSIVE edges)
    """

    def test_no_cycles_linear(self):
        """No cycles in a linear workflow (A -> B -> C)."""
        wf = WorkflowDefinition(
            name='Linear',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'c'},
            ],
            entry='a',
            exit='c',
        )
        result = check_cycles(wf)
        assert result.passed is True
        assert result.message == 'No cycles detected'

    def test_cycle_detected_simple(self):
        """Simple cycle detected (A -> B -> A)."""
        wf = WorkflowDefinition(
            name='Simple Cycle',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'a'},  # Back edge creates cycle
            ],
            entry='a',
            exit='b',
        )
        result = check_cycles(wf)
        assert result.passed is False
        assert 'cycle' in result.message.lower()
        assert result.details is not None
        assert 'cycle_path' in result.details

    def test_cycle_detected_complex(self):
        """Cycle detected in complex graph (A -> B -> C -> A)."""
        wf = WorkflowDefinition(
            name='Complex Cycle',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'c'},
                {'from': 'c', 'to': 'a'},  # Back edge
            ],
            entry='a',
            exit='c',
        )
        result = check_cycles(wf)
        assert result.passed is False

    def test_recursive_edge_allowed(self):
        """RECURSIVE edge type is allowed to create a loop."""
        wf = WorkflowDefinition(
            name='Recursive Workflow',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'c'},
                {'from': 'b', 'to': 'a', 'type': 'RECURSIVE'},  # Allowed loop
            ],
            entry='a',
            exit='c',
        )
        result = check_cycles(wf)
        assert result.passed is True

    def test_self_loop_detected(self):
        """Self-loop detected (A -> A)."""
        wf = WorkflowDefinition(
            name='Self Loop',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'a'},  # Self-loop
            ],
            entry='a',
            exit='a',
        )
        result = check_cycles(wf)
        assert result.passed is False
        assert 'cycle' in result.message.lower()

    def test_no_cycles_dag(self):
        """No cycles in a DAG with multiple paths."""
        wf = WorkflowDefinition(
            name='DAG',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'c': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'test'},
                },
                'd': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'a', 'to': 'c'},
                {'from': 'b', 'to': 'd'},
                {'from': 'c', 'to': 'd'},
            ],
            entry='a',
            exit='d',
        )
        result = check_cycles(wf)
        assert result.passed is True


class TestVariableReferenceValidation:
    """
    Scenario: Variable reference validation
    Given node configs with {{slug.output.field}} references
    When variable references are validated
    Then all referenced slugs must exist as defined nodes
    """

    def test_all_variable_refs_valid(self):
        """All variable references point to existing nodes."""
        wf = WorkflowDefinition(
            name='Valid Refs',
            nodes={
                'input': {
                    'type': 'plain_txt_input',
                    'execution_mode': 'INPUT',
                    'config': {'placeholder': 'Enter text'},
                },
                'process': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'Process: {{input.output.text}}'},
                },
                'output': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[
                {'from': 'input', 'to': 'process'},
                {'from': 'process', 'to': 'output'},
            ],
            entry='input',
            exit='output',
        )
        result = check_variable_references(wf)
        assert result.passed is True
        assert result.message == 'All variable references are valid'

    def test_invalid_variable_ref_nonexistent_slug(self):
        """Variable reference points to non-existent node slug."""
        wf = WorkflowDefinition(
            name='Invalid Ref',
            nodes={
                'input': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'process': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': 'Process: {{nonexistent.output.data}}'},
                },
            },
            edges=[{'from': 'input', 'to': 'process'}],
            entry='input',
            exit='process',
        )
        result = check_variable_references(wf)
        assert result.passed is False
        assert 'nonexistent' in result.message
        assert result.details is not None
        assert 'invalid_references' in result.details
        assert len(result.details['invalid_references']) == 1

    def test_multiple_valid_refs(self):
        """Multiple variable references all point to existing nodes."""
        wf = WorkflowDefinition(
            name='Multiple Refs',
            nodes={
                'a': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'b': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': '{{a.output.text}}'},
                },
                'c': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {
                        'model': 'test',
                        'template': '{{a.output.text}} and {{b.output.result}}',
                    },
                },
            },
            edges=[
                {'from': 'a', 'to': 'b'},
                {'from': 'b', 'to': 'c'},
            ],
            entry='a',
            exit='c',
        )
        result = check_variable_references(wf)
        assert result.passed is True

    def test_multiple_invalid_refs(self):
        """Multiple invalid variable references detected."""
        wf = WorkflowDefinition(
            name='Multiple Invalid',
            nodes={
                'input': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'process': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {
                        'model': 'test',
                        'template': '{{missing1.output.x}} and {{missing2.output.y}}',
                    },
                },
            },
            edges=[{'from': 'input', 'to': 'process'}],
            entry='input',
            exit='process',
        )
        result = check_variable_references(wf)
        assert result.passed is False
        assert result.details is not None
        assert len(result.details['invalid_references']) == 2

    def test_no_variable_refs(self):
        """Workflow with no variable references passes validation."""
        wf = WorkflowDefinition(
            name='No Refs',
            nodes={
                'input': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'output': {'type': 'structured_output', 'execution_mode': 'OUTPUT', 'config': {}},
            },
            edges=[{'from': 'input', 'to': 'output'}],
            entry='input',
            exit='output',
        )
        result = check_variable_references(wf)
        assert result.passed is True

    def test_nested_config_variable_refs(self):
        """Variable references in nested config structures are validated."""
        wf = WorkflowDefinition(
            name='Nested Refs',
            nodes={
                'input': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'extract': {
                    'type': 'document_extraction',
                    'execution_mode': 'MESSAGES',
                    'config': {
                        'fields': [
                            {
                                'name': 'title',
                                'type': 'string',
                                'required': True,
                            },
                        ],
                        'prompt': '{{input.output.text}}',
                    },
                },
            },
            edges=[{'from': 'input', 'to': 'extract'}],
            entry='input',
            exit='extract',
        )
        result = check_variable_references(wf)
        assert result.passed is True

    def test_variable_ref_with_hyphens(self):
        """Variable references with hyphens in slug are valid."""
        wf = WorkflowDefinition(
            name='Hyphenated Slug',
            nodes={
                'user-input': {'type': 'plain_txt_input', 'execution_mode': 'INPUT', 'config': {}},
                'process': {
                    'type': 'llm_call',
                    'execution_mode': 'MESSAGES',
                    'config': {'model': 'test', 'template': '{{user-input.output.text}}'},
                },
            },
            edges=[{'from': 'user-input', 'to': 'process'}],
            entry='user-input',
            exit='process',
        )
        result = check_variable_references(wf)
        assert result.passed is True
