"""Unit tests for the contract validation module (contract.py).

Tests alias mapping, JSON Schema validation, error formatting,
CLI-only field stripping, and schema $defs merging.

Reference: Phase 46 - Push-Time Contract Validation
"""

from types import SimpleNamespace

from cli.contract import (
    ContractError,
    _merge_schema_defs,
    _suggest_fix,
    _transform_config,
    validate_contract,
    validate_node_contract,
)

# ---------------------------------------------------------------------------
# Fixtures: Simplified JSON Schemas matching Pydantic model_json_schema() output
# ---------------------------------------------------------------------------

SAMPLE_AGENT_SCHEMA = {
    'type': 'object',
    'properties': {
        'model_name': {
            'anyOf': [{'type': 'string'}, {'type': 'null'}],
            'default': None,
            'title': 'Model Name',
        },
        'max_tokens': {
            'anyOf': [{'type': 'integer', 'exclusiveMinimum': 0}, {'type': 'null'}],
            'default': None,
            'title': 'Max Tokens',
        },
        'temperature': {
            'anyOf': [{'type': 'number', 'minimum': 0.0, 'maximum': 2.0}, {'type': 'null'}],
            'default': None,
            'title': 'Temperature',
        },
        'agent_name': {
            'anyOf': [{'type': 'string'}, {'type': 'null'}],
            'default': None,
            'title': 'Agent Name',
        },
        'system_prompt': {
            'anyOf': [{'type': 'string'}, {'type': 'null'}],
            'default': None,
            'title': 'System Prompt',
        },
    },
    'title': 'AgentNodeConfig',
}

SAMPLE_LLM_SCHEMA = {
    'type': 'object',
    'properties': {
        'model_name': {'type': 'string', 'title': 'Model Name'},
        'system_prompt': {'type': 'string', 'title': 'System Prompt'},
        'max_tokens': {
            'anyOf': [{'type': 'integer', 'exclusiveMinimum': 0}, {'type': 'null'}],
            'default': None,
        },
        'temperature': {
            'anyOf': [{'type': 'number', 'minimum': 0.0, 'maximum': 2.0}, {'type': 'null'}],
            'default': None,
        },
    },
    'required': ['model_name', 'system_prompt'],
    'title': 'LLMNodeConfig',
}

SAMPLE_REGISTRY = {
    'all_node_types': [
        {
            'type': 'AGENT',
            'status': 'active',
            'config_json_schema': SAMPLE_AGENT_SCHEMA,
            'output_variables': ['text', 'metadata'],
            'config_fields': ['model_name', 'max_tokens', 'temperature'],
        },
        {
            'type': 'LLM_CALL',
            'status': 'active',
            'config_json_schema': SAMPLE_LLM_SCHEMA,
            'output_variables': ['text'],
            'config_fields': ['model_name', 'system_prompt', 'max_tokens', 'temperature'],
        },
    ],
    'schema_definitions': {},
}


# ---------------------------------------------------------------------------
# TestContractError
# ---------------------------------------------------------------------------


class TestContractError:
    """Test ContractError dataclass attributes."""

    def test_attributes(self):
        """ContractError has node_slug, node_type, field, message, suggestion."""
        error = ContractError(
            node_slug='my_node',
            node_type='AGENT',
            field='temperature',
            message='is not of type "number"',
            suggestion='Change the value to type "number"',
        )
        assert error.node_slug == 'my_node'
        assert error.node_type == 'AGENT'
        assert error.field == 'temperature'
        assert error.message == 'is not of type "number"'
        assert error.suggestion == 'Change the value to type "number"'

    def test_suggestion_defaults_to_none(self):
        """ContractError suggestion defaults to None when not provided."""
        error = ContractError(
            node_slug='n',
            node_type='AGENT',
            field='f',
            message='m',
        )
        assert error.suggestion is None


# ---------------------------------------------------------------------------
# TestTransformConfig
# ---------------------------------------------------------------------------


class TestTransformConfig:
    """Test _transform_config applies aliases per node type and strips CLI-only fields."""

    def test_agent_aliases_applied(self):
        """AGENT: 'model' -> 'model_name', 'maxTokens' -> 'max_tokens'."""
        config = {'model': 'gpt-4', 'maxTokens': 1000, 'temperature': 0.5}
        result = _transform_config('AGENT', config)
        assert result == {'model_name': 'gpt-4', 'max_tokens': 1000, 'temperature': 0.5}

    def test_llm_call_aliases_applied(self):
        """LLM_CALL: 'model' -> 'model_name', 'template' -> 'system_prompt'."""
        config = {'model': 'gpt-4', 'template': 'You are a bot', 'temperature': 0.7}
        result = _transform_config('LLM_CALL', config)
        assert result == {
            'model_name': 'gpt-4',
            'system_prompt': 'You are a bot',
            'temperature': 0.7,
        }

    def test_unknown_node_type_returns_config_unchanged(self):
        """Unknown node type returns config dict unchanged (no aliases)."""
        config = {'model': 'gpt-4', 'temperature': 0.5}
        result = _transform_config('UNKNOWN_TYPE', config)
        assert result == config

    def test_cli_only_fields_stripped_agent(self):
        """AGENT: CLI-only fields (agent_name, agentId, primaryInput, tools) are stripped."""
        config = {
            'model': 'gpt-4',
            'agent_name': 'My Agent',
            'agentId': 'uuid-123',
            'primaryInput': '{{input.output.text}}',
            'tools': ['web_search'],
            'temperature': 0.5,
        }
        result = _transform_config('AGENT', config)
        assert 'agent_name' not in result
        assert 'agentId' not in result
        assert 'primaryInput' not in result
        assert 'tools' not in result
        assert result['model_name'] == 'gpt-4'
        assert result['temperature'] == 0.5

    def test_cli_only_fields_stripped_rag_agent(self):
        """RAG_AGENT: CLI-only fields (agent_name, knowledge_base_names, etc.) are stripped."""
        config = {
            'agent_name': 'RAG Bot',
            'knowledge_base_names': ['kb1'],
            'primaryInput': '{{input.output.text}}',
            'agentId': 'uuid',
            'knowledgeBaseIds': ['kb-uuid'],
        }
        result = _transform_config('RAG_AGENT', config)
        assert 'agent_name' not in result
        assert 'knowledge_base_names' not in result
        assert 'primaryInput' not in result
        assert 'agentId' not in result
        assert 'knowledgeBaseIds' not in result

    def test_cli_only_fields_stripped_retrieve(self):
        """RETRIEVE: CLI-only fields (knowledge_base_name, knowledge_base_names, etc.) stripped."""
        config = {
            'knowledgeBaseId': 'kb-uuid',
            'knowledge_base_name': 'my-kb',
            'knowledge_base_names': ['kb1'],
            'topK': 5,
            'searchQuery': '{{input.output.text}}',
        }
        result = _transform_config('RETRIEVE', config)
        assert 'knowledgeBaseId' not in result
        assert 'knowledge_base_name' not in result
        assert 'knowledge_base_names' not in result
        assert 'searchQuery' not in result
        assert result['top_k_results'] == 5

    def test_case_insensitive_node_type(self):
        """Node type lookup is case-insensitive (uppercased internally)."""
        config = {'model': 'gpt-4'}
        result = _transform_config('agent', config)
        assert result == {'model_name': 'gpt-4'}


# ---------------------------------------------------------------------------
# TestMergeSchemaDefinitions
# ---------------------------------------------------------------------------


class TestMergeSchemaDefinitions:
    """Test _merge_schema_defs merges schema_definitions into schema $defs."""

    def test_merge_defs(self):
        """schema_definitions are merged into schema's $defs."""
        schema = {'type': 'object', 'properties': {}}
        defs = {'ExtractionField': {'type': 'object', 'properties': {'name': {'type': 'string'}}}}
        result = _merge_schema_defs(schema, defs)
        assert '$defs' in result
        assert 'ExtractionField' in result['$defs']

    def test_empty_defs_returns_schema_unchanged(self):
        """Empty schema_definitions returns schema unchanged."""
        schema = {'type': 'object', 'properties': {}}
        result = _merge_schema_defs(schema, {})
        assert result is schema  # Same object, no copy needed

    def test_existing_defs_not_overwritten(self):
        """Existing $defs in schema take precedence over schema_definitions."""
        schema = {
            'type': 'object',
            '$defs': {'MyType': {'type': 'string'}},
        }
        defs = {'MyType': {'type': 'integer'}, 'OtherType': {'type': 'boolean'}}
        result = _merge_schema_defs(schema, defs)
        # Schema's own $defs should win for MyType
        assert result['$defs']['MyType'] == {'type': 'string'}
        # But OtherType from schema_definitions should be added
        assert result['$defs']['OtherType'] == {'type': 'boolean'}


# ---------------------------------------------------------------------------
# TestValidateNodeContract
# ---------------------------------------------------------------------------


class TestValidateNodeContract:
    """Test validate_node_contract validates one node's config against a schema."""

    def test_valid_config_returns_empty_list(self):
        """Valid AGENT config returns empty error list."""
        config = {'model_name': 'gpt-4', 'temperature': 0.5}
        errors = validate_node_contract('my_agent', 'AGENT', config, SAMPLE_AGENT_SCHEMA)
        assert errors == []

    def test_type_mismatch_returns_error(self):
        """Type mismatch in temperature returns ContractError."""
        config = {'temperature': 'not_a_number'}
        errors = validate_node_contract('my_agent', 'AGENT', config, SAMPLE_AGENT_SCHEMA)
        assert len(errors) >= 1
        temp_error = next((e for e in errors if e.field == 'temperature'), None)
        assert temp_error is not None
        assert 'is not valid under any of the given schemas' in temp_error.message

    def test_missing_required_field_returns_error(self):
        """Missing required field (model_name) for LLM_CALL returns ContractError."""
        config = {'system_prompt': 'You are a bot'}
        errors = validate_node_contract('my_llm', 'LLM_CALL', config, SAMPLE_LLM_SCHEMA)
        assert len(errors) >= 1
        req_error = next((e for e in errors if 'model_name' in e.message), None)
        assert req_error is not None
        assert req_error.field == '(root)'

    def test_alias_mapping_applied_before_validation(self):
        """WDF 'model' alias is transformed to 'model_name' before validation."""
        # 'model' in WDF -> 'model_name' in schema; 'template' -> 'system_prompt'
        config = {'model': 'gpt-4', 'template': 'Hello'}
        errors = validate_node_contract('my_llm', 'LLM_CALL', config, SAMPLE_LLM_SCHEMA)
        # Should pass because aliases transform model->model_name, template->system_prompt
        assert errors == []


# ---------------------------------------------------------------------------
# TestValidateContract
# ---------------------------------------------------------------------------


class TestValidateContract:
    """Test validate_contract iterates all workflow nodes and aggregates errors."""

    def test_full_workflow_valid(self):
        """Valid workflow with correct configs returns no errors."""
        workflow = SimpleNamespace(
            nodes={
                'agent1': SimpleNamespace(
                    type='agent',
                    config={'model_name': 'gpt-4', 'temperature': 0.5},
                ),
            }
        )
        errors = validate_contract(workflow, SAMPLE_REGISTRY)
        assert errors == []

    def test_unknown_node_type_skipped(self):
        """Nodes with types not in registry are skipped (no error)."""
        workflow = SimpleNamespace(
            nodes={
                'custom': SimpleNamespace(
                    type='custom_type',
                    config={'anything': 'goes'},
                ),
            }
        )
        errors = validate_contract(workflow, SAMPLE_REGISTRY)
        assert errors == []

    def test_multiple_errors_aggregated(self):
        """Errors from multiple nodes are aggregated into one list."""
        workflow = SimpleNamespace(
            nodes={
                'llm1': SimpleNamespace(
                    type='llm_call',
                    config={},  # Missing required model_name and system_prompt
                ),
                'llm2': SimpleNamespace(
                    type='llm_call',
                    config={},  # Also missing required fields
                ),
            }
        )
        errors = validate_contract(workflow, SAMPLE_REGISTRY)
        # Both nodes should produce errors for missing required fields
        assert len(errors) >= 2
        # Errors from both nodes should be present
        node_slugs = {e.node_slug for e in errors}
        assert 'llm1' in node_slugs
        assert 'llm2' in node_slugs

    def test_mixed_valid_and_invalid_nodes(self):
        """Workflow with both valid and invalid nodes returns only errors for invalid ones."""
        workflow = SimpleNamespace(
            nodes={
                'good_agent': SimpleNamespace(
                    type='agent',
                    config={'model_name': 'gpt-4'},
                ),
                'bad_llm': SimpleNamespace(
                    type='llm_call',
                    config={},  # Missing required fields
                ),
            }
        )
        errors = validate_contract(workflow, SAMPLE_REGISTRY)
        assert all(e.node_slug == 'bad_llm' for e in errors)


# ---------------------------------------------------------------------------
# TestSuggestFix
# ---------------------------------------------------------------------------


class TestSuggestFix:
    """Test _suggest_fix returns human-readable suggestions."""

    def test_required_field_suggestion(self):
        """Required field error produces 'Add the required field' suggestion."""
        import jsonschema

        schema = SAMPLE_LLM_SCHEMA
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors({}))
        req_error = next((e for e in errors if e.validator == 'required'), None)
        assert req_error is not None
        suggestion = _suggest_fix(req_error, 'LLM_CALL')
        assert suggestion is not None
        assert 'Add the required field' in suggestion
        assert 'LLM_CALL' in suggestion

    def test_type_error_suggestion(self):
        """Type error produces 'Change the value to type' suggestion."""
        import jsonschema

        schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors({'name': 123}))
        assert len(errors) >= 1
        suggestion = _suggest_fix(errors[0], 'AGENT')
        assert suggestion is not None
        assert 'Change the value to type' in suggestion
        assert 'string' in suggestion

    def test_anyof_suggestion(self):
        """anyOf error produces 'Expected X or Y' suggestion."""
        import jsonschema

        schema = {
            'type': 'object',
            'properties': {
                'temperature': {
                    'anyOf': [
                        {'type': 'number'},
                        {'type': 'null'},
                    ]
                }
            },
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors({'temperature': 'bad'}))
        assert len(errors) >= 1
        temp_error = next((e for e in errors if e.validator == 'anyOf'), None)
        assert temp_error is not None
        suggestion = _suggest_fix(temp_error, 'AGENT')
        assert suggestion is not None
        assert 'number' in suggestion

    def test_unknown_validator_returns_none(self):
        """Unknown validator type returns None."""
        import jsonschema

        # Create a mock-like validation error for an uncommon validator
        schema = {
            'type': 'object',
            'properties': {'name': {'type': 'string', 'minLength': 3}},
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors({'name': 'ab'}))
        minlen_error = next((e for e in errors if e.validator == 'minLength'), None)
        if minlen_error:
            suggestion = _suggest_fix(minlen_error, 'AGENT')
            assert suggestion is None


# ---------------------------------------------------------------------------
# TestSchemaDefsMerge (integration-level)
# ---------------------------------------------------------------------------


class TestSchemaDefsMergeIntegration:
    """Test that schema_definitions from registry are merged during validation."""

    def test_schema_defs_merge_in_validate_contract(self):
        """validate_contract merges schema_definitions into each node's schema."""
        # Create a schema that uses a $ref to a type defined in schema_definitions
        schema_with_ref = {
            'type': 'object',
            'properties': {
                'model_name': {'$ref': '#/$defs/ModelName'},
            },
        }
        registry = {
            'all_node_types': [
                {
                    'type': 'AGENT',
                    'status': 'active',
                    'config_json_schema': schema_with_ref,
                },
            ],
            'schema_definitions': {
                'ModelName': {'type': 'string'},
            },
        }
        workflow = SimpleNamespace(
            nodes={
                'agent1': SimpleNamespace(
                    type='agent',
                    config={'model_name': 'gpt-4'},
                ),
            }
        )
        # Should not raise (the $ref is resolved via merged defs)
        errors = validate_contract(workflow, registry)
        assert errors == []
