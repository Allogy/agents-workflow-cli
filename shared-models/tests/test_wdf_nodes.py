"""Tests for WDF node config schemas.

Verifies that all 10 node type config schemas enforce required fields,
apply correct defaults, and reject invalid values with clear error messages.

BDD Scenario from RAG-945:
  Given the WDF schema is defined
  When a YAML file uses any of the 10 node types
  Then the config block is validated against the node type's schema
  And required fields are enforced
  And invalid values are rejected with clear error messages
"""

import pytest
from pydantic import ValidationError

from workflow_models.wdf.nodes import (
    AgentConfig,
    DocumentExtractionConfig,
    ExtractionField,
    FileUploadConfig,
    HumanReviewConfig,
    LlmCallConfig,
    NodeDefinition,
    PlainTxtInputConfig,
    RagAgentConfig,
    RetrieveConfig,
    StructuredInputConfig,
    StructuredOutputConfig,
)

# ============================================
# PLAIN_TXT_INPUT Config
# ============================================


class TestPlainTxtInputConfig:
    """PLAIN_TXT_INPUT: placeholder (optional)."""

    def test_empty_config_valid(self):
        """All fields are optional — empty config is valid."""
        config = PlainTxtInputConfig()
        assert config.placeholder is None

    def test_with_placeholder(self):
        config = PlainTxtInputConfig(placeholder='Type your question here...')
        assert config.placeholder == 'Type your question here...'


# ============================================
# STRUCTURED_INPUT Config
# ============================================


class TestStructuredInputConfig:
    """STRUCTURED_INPUT: schema (JSON Schema object, required)."""

    def test_valid_with_schema(self):
        config = StructuredInputConfig(
            schema={
                'type': 'object',
                'required': ['name', 'email'],
                'properties': {
                    'name': {'type': 'string', 'minLength': 1},
                    'email': {'type': 'string', 'format': 'email'},
                },
            }
        )
        assert config.schema_['type'] == 'object'

    def test_missing_schema_raises(self):
        """schema is required — omitting it must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            StructuredInputConfig()  # type: ignore[call-arg]
        assert 'schema' in str(exc_info.value).lower()

    def test_schema_must_be_dict(self):
        with pytest.raises(ValidationError):
            StructuredInputConfig(schema='not a dict')  # type: ignore[arg-type]


# ============================================
# FILE_UPLOAD Config
# ============================================


class TestFileUploadConfig:
    """FILE_UPLOAD: acceptedFormats, maxFileSize (required); textExtraction, extractTables (optional)."""

    def test_valid_full_config(self):
        config = FileUploadConfig(
            acceptedFormats=['pdf', 'png', 'jpg'],
            maxFileSize=10485760,
            textExtraction='automatic',
            extractTables=True,
        )
        assert config.acceptedFormats == ['pdf', 'png', 'jpg']
        assert config.maxFileSize == 10485760
        assert config.textExtraction == 'automatic'
        assert config.extractTables is True

    def test_minimal_required_fields(self):
        config = FileUploadConfig(
            acceptedFormats=['pdf'],
            maxFileSize=5242880,
        )
        assert config.acceptedFormats == ['pdf']
        assert config.maxFileSize == 5242880
        assert config.textExtraction is None
        assert config.extractTables is None
        assert config.preserveFormatting is None

    def test_missing_accepted_formats_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            FileUploadConfig(maxFileSize=1024)  # type: ignore[call-arg]
        assert 'acceptedFormats' in str(exc_info.value) or 'accepted_formats' in str(exc_info.value)

    def test_missing_max_file_size_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            FileUploadConfig(acceptedFormats=['pdf'])  # type: ignore[call-arg]
        assert 'maxFileSize' in str(exc_info.value) or 'max_file_size' in str(exc_info.value)

    def test_max_file_size_must_be_positive(self):
        with pytest.raises(ValidationError):
            FileUploadConfig(acceptedFormats=['pdf'], maxFileSize=-1)

    def test_accepted_formats_must_be_list(self):
        with pytest.raises(ValidationError):
            FileUploadConfig(acceptedFormats='pdf', maxFileSize=1024)  # type: ignore[arg-type]


# ============================================
# AGENT Config
# ============================================


class TestAgentConfig:
    """AGENT: model, system_prompt (required); temperature, maxTokens, tools, agentId (optional)."""

    def test_valid_minimal(self):
        config = AgentConfig(
            model='us.anthropic.claude-sonnet-4-20250514-v1:0',
            system_prompt='You are a helpful assistant.',
        )
        assert config.model == 'us.anthropic.claude-sonnet-4-20250514-v1:0'
        assert config.system_prompt == 'You are a helpful assistant.'
        assert config.temperature is None
        assert config.maxTokens is None
        assert config.tools is None
        assert config.agentId is None

    def test_valid_full_config(self):
        config = AgentConfig(
            model='us.anthropic.claude-sonnet-4-20250514-v1:0',
            system_prompt='You are a helpful assistant.',
            temperature=0.7,
            maxTokens=2048,
            tools=[],
            agentId='general-agent',
        )
        assert config.temperature == 0.7
        assert config.maxTokens == 2048
        assert config.tools == []
        assert config.agentId == 'general-agent'

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError):
            AgentConfig(system_prompt='test')  # type: ignore[call-arg]

    def test_missing_system_prompt_raises(self):
        with pytest.raises(ValidationError):
            AgentConfig(model='test')  # type: ignore[call-arg]

    def test_temperature_range(self):
        """Temperature should be between 0 and 2."""
        config = AgentConfig(model='test', system_prompt='test', temperature=0.0)
        assert config.temperature == 0.0

        config = AgentConfig(model='test', system_prompt='test', temperature=2.0)
        assert config.temperature == 2.0

        with pytest.raises(ValidationError):
            AgentConfig(model='test', system_prompt='test', temperature=-0.1)

        with pytest.raises(ValidationError):
            AgentConfig(model='test', system_prompt='test', temperature=2.1)


# ============================================
# RAG_AGENT Config
# ============================================


class TestRagAgentConfig:
    """RAG_AGENT: agentId, knowledgeBaseIds (required); primaryInput (optional)."""

    def test_valid_config(self):
        config = RagAgentConfig(
            agentId='kb-agent',
            knowledgeBaseIds=['kb-1', 'kb-2'],
        )
        assert config.agentId == 'kb-agent'
        assert config.knowledgeBaseIds == ['kb-1', 'kb-2']
        assert config.primaryInput is None

    def test_with_primary_input(self):
        config = RagAgentConfig(
            agentId='kb-agent',
            knowledgeBaseIds=['kb-1'],
            primaryInput='{{llmPrompt_1.output.text}}',
        )
        assert config.primaryInput == '{{llmPrompt_1.output.text}}'

    def test_missing_agent_id_raises(self):
        with pytest.raises(ValidationError):
            RagAgentConfig(knowledgeBaseIds=['kb-1'])  # type: ignore[call-arg]

    def test_missing_knowledge_base_ids_raises(self):
        with pytest.raises(ValidationError):
            RagAgentConfig(agentId='kb-agent')  # type: ignore[call-arg]

    def test_knowledge_base_ids_must_be_list(self):
        with pytest.raises(ValidationError):
            RagAgentConfig(agentId='agent', knowledgeBaseIds='kb-1')  # type: ignore[arg-type]


# ============================================
# LLM_CALL Config
# ============================================


class TestLlmCallConfig:
    """LLM_CALL: model, template (required); system_prompt, temperature, maxTokens, topP (optional)."""

    def test_valid_minimal(self):
        config = LlmCallConfig(
            model='anthropic.claude-sonnet-4-5-v2',
            template='Analyze the following: {{input.output.data}}',
        )
        assert config.model == 'anthropic.claude-sonnet-4-5-v2'
        assert config.template == 'Analyze the following: {{input.output.data}}'
        assert config.system_prompt is None
        assert config.temperature is None
        assert config.maxTokens is None
        assert config.topP is None

    def test_valid_full_config(self):
        config = LlmCallConfig(
            model='anthropic.claude-sonnet-4-5-v2',
            temperature=0.3,
            maxTokens=2048,
            topP=0.9,
            template='Classify: {{extract.output.extractedData}}',
            system_prompt='You are a helpful assistant.',
        )
        assert config.temperature == 0.3
        assert config.maxTokens == 2048
        assert config.topP == 0.9
        assert config.system_prompt == 'You are a helpful assistant.'

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError):
            LlmCallConfig(template='some template')  # type: ignore[call-arg]

    def test_missing_template_raises(self):
        with pytest.raises(ValidationError):
            LlmCallConfig(model='anthropic.claude-sonnet-4-5-v2')  # type: ignore[call-arg]

    def test_temperature_range(self):
        """Temperature should be between 0 and 2."""
        config = LlmCallConfig(model='test', template='test', temperature=0.0)
        assert config.temperature == 0.0

        config = LlmCallConfig(model='test', template='test', temperature=2.0)
        assert config.temperature == 2.0

        with pytest.raises(ValidationError):
            LlmCallConfig(model='test', template='test', temperature=-0.1)

        with pytest.raises(ValidationError):
            LlmCallConfig(model='test', template='test', temperature=2.1)

    def test_max_tokens_must_be_positive(self):
        with pytest.raises(ValidationError):
            LlmCallConfig(model='test', template='test', maxTokens=0)

    def test_top_p_range(self):
        """topP should be between 0 and 1."""
        with pytest.raises(ValidationError):
            LlmCallConfig(model='test', template='test', topP=-0.1)

        with pytest.raises(ValidationError):
            LlmCallConfig(model='test', template='test', topP=1.1)


# ============================================
# STRUCTURED_OUTPUT Config
# ============================================


class TestStructuredOutputConfig:
    """STRUCTURED_OUTPUT: schema (JSON Schema, optional), model (optional)."""

    def test_empty_config_valid(self):
        """All fields are optional."""
        config = StructuredOutputConfig()
        assert config.schema_ is None
        assert config.model is None

    def test_with_schema(self):
        config = StructuredOutputConfig(
            model='anthropic.claude-sonnet-4-5-v2',
            schema={
                'type': 'object',
                'properties': {
                    'summary': {'type': 'string'},
                    'confidence': {'type': 'number'},
                },
            },
        )
        assert config.model == 'anthropic.claude-sonnet-4-5-v2'
        assert config.schema_['type'] == 'object'


# ============================================
# RETRIEVE Config
# ============================================


class TestRetrieveConfig:
    """RETRIEVE: knowledgeBaseId (required), topK, scoreThreshold, enableReranking, includeMetadata (optional)."""

    def test_valid_minimal(self):
        config = RetrieveConfig(knowledgeBaseId='my-kb')
        assert config.knowledgeBaseId == 'my-kb'
        assert config.topK is None
        assert config.scoreThreshold is None
        assert config.enableReranking is None
        assert config.includeMetadata is None

    def test_valid_full_config(self):
        config = RetrieveConfig(
            knowledgeBaseId='my-kb',
            topK=5,
            scoreThreshold=0.7,
            enableReranking=False,
            includeMetadata=True,
        )
        assert config.topK == 5
        assert config.scoreThreshold == 0.7
        assert config.enableReranking is False
        assert config.includeMetadata is True

    def test_missing_knowledge_base_id_raises(self):
        with pytest.raises(ValidationError):
            RetrieveConfig()  # type: ignore[call-arg]

    def test_top_k_must_be_positive(self):
        with pytest.raises(ValidationError):
            RetrieveConfig(knowledgeBaseId='kb', topK=0)

    def test_score_threshold_range(self):
        """scoreThreshold should be between 0 and 1."""
        with pytest.raises(ValidationError):
            RetrieveConfig(knowledgeBaseId='kb', scoreThreshold=-0.1)

        with pytest.raises(ValidationError):
            RetrieveConfig(knowledgeBaseId='kb', scoreThreshold=1.1)


# ============================================
# DOCUMENT_EXTRACTION Config
# ============================================


class TestDocumentExtractionConfig:
    """DOCUMENT_EXTRACTION: fields (default empty); extractionMethod, prompt, extractTables, extractImages (optional)."""

    def test_empty_config_valid(self):
        """Fields default to empty list — empty config is valid."""
        config = DocumentExtractionConfig()
        assert config.fields == []
        assert config.extractionMethod is None
        assert config.prompt is None
        assert config.extractTables is None
        assert config.extractImages is None

    def test_with_fields(self):
        config = DocumentExtractionConfig(
            fields=[
                ExtractionField(name='vendor_name', type='string', required=True),
                ExtractionField(name='total_amount', type='number', required=True),
            ]
        )
        assert len(config.fields) == 2
        assert config.fields[0].name == 'vendor_name'
        assert config.fields[0].required is True

    def test_valid_full_config(self):
        config = DocumentExtractionConfig(
            extractionMethod='llm',
            fields=[
                ExtractionField(name='title', type='string', required=True),
                ExtractionField(name='date', type='string', required=False),
            ],
            prompt='Extract the title and date from this document.',
            extractTables=True,
            extractImages=False,
        )
        assert config.extractionMethod == 'llm'
        assert config.prompt == 'Extract the title and date from this document.'
        assert config.extractTables is True
        assert config.extractImages is False

    def test_extraction_field_requires_name_and_type(self):
        with pytest.raises(ValidationError):
            ExtractionField(name='test')  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            ExtractionField(type='string')  # type: ignore[call-arg]

    def test_extraction_field_required_defaults_false(self):
        field = ExtractionField(name='optional_field', type='string')
        assert field.required is False


# ============================================
# HUMAN_REVIEW Config
# ============================================


class TestHumanReviewConfig:
    """HUMAN_REVIEW: review_prompt, timeoutMinutes, allowApprove, allowReject, allowEdit (all optional)."""

    def test_empty_config_valid(self):
        config = HumanReviewConfig()
        assert config.review_prompt is None
        assert config.timeoutMinutes is None
        assert config.allowApprove is None
        assert config.allowReject is None
        assert config.allowEdit is None

    def test_with_all_fields(self):
        config = HumanReviewConfig(
            review_prompt='Please review and approve to continue.',
            timeoutMinutes=1440,
            allowApprove=True,
            allowReject=True,
            allowEdit=False,
        )
        assert config.review_prompt == 'Please review and approve to continue.'
        assert config.timeoutMinutes == 1440
        assert config.allowApprove is True
        assert config.allowReject is True
        assert config.allowEdit is False

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            HumanReviewConfig(timeoutMinutes=0)

        with pytest.raises(ValidationError):
            HumanReviewConfig(timeoutMinutes=-10)


# ============================================
# NodeDefinition (wrapper)
# ============================================


class TestNodeDefinition:
    """NodeDefinition wraps type + execution_mode + label + config for any node type."""

    def test_plain_txt_input_node(self):
        node = NodeDefinition(
            type='plain_txt_input',
            execution_mode='INPUT',
            label='Enter Question',
            config={'placeholder': 'Type your question here...'},
        )
        assert node.type == 'plain_txt_input'
        assert node.execution_mode == 'INPUT'
        assert node.label == 'Enter Question'
        assert isinstance(node.parsed_config, PlainTxtInputConfig)
        assert node.parsed_config.placeholder == 'Type your question here...'

    def test_agent_node(self):
        node = NodeDefinition(
            type='agent',
            execution_mode='MESSAGES',
            label='Research Agent',
            config={
                'model': 'us.anthropic.claude-sonnet-4-20250514-v1:0',
                'system_prompt': 'You are a helpful assistant.',
            },
        )
        assert node.type == 'agent'
        assert node.execution_mode == 'MESSAGES'
        assert isinstance(node.parsed_config, AgentConfig)
        assert node.parsed_config.model == 'us.anthropic.claude-sonnet-4-20250514-v1:0'

    def test_llm_call_node(self):
        node = NodeDefinition(
            type='llm_call',
            execution_mode='MESSAGES',
            label='Classify',
            config={
                'model': 'anthropic.claude-sonnet-4-5-v2',
                'template': 'Classify: {{data}}',
            },
        )
        assert isinstance(node.parsed_config, LlmCallConfig)

    def test_file_upload_node(self):
        node = NodeDefinition(
            type='file_upload',
            execution_mode='INPUT',
            label='Upload Document',
            config={
                'acceptedFormats': ['pdf', 'docx'],
                'maxFileSize': 10485760,
            },
        )
        assert isinstance(node.parsed_config, FileUploadConfig)

    def test_unknown_node_type_raises(self):
        """Unknown node types should be rejected."""
        with pytest.raises(ValidationError):
            NodeDefinition(
                type='unknown_type',
                execution_mode='INPUT',
                label='Bad Node',
                config={},
            )

    def test_invalid_execution_mode_raises(self):
        """Invalid execution modes should be rejected."""
        with pytest.raises(ValidationError):
            NodeDefinition(
                type='plain_txt_input',
                execution_mode='INVALID',
                config={},
            )

    def test_missing_execution_mode_raises(self):
        """execution_mode is required."""
        with pytest.raises(ValidationError):
            NodeDefinition(
                type='plain_txt_input',
                config={},
            )  # type: ignore[call-arg]

    def test_invalid_config_for_type_raises(self):
        """Config must match the declared node type's schema."""
        with pytest.raises(ValidationError):
            NodeDefinition(
                type='llm_call',
                execution_mode='MESSAGES',
                label='Bad LLM',
                config={'placeholder': 'wrong config for llm_call'},
            )

    def test_label_is_optional(self):
        node = NodeDefinition(
            type='human_review',
            execution_mode='FLOW',
            config={},
        )
        assert node.label is None

    def test_all_10_node_types_recognized(self):
        """Verify all 10 node types from the ticket can be created."""
        valid_types = [
            ('plain_txt_input', 'INPUT', {}),
            ('structured_input', 'INPUT', {'schema': {'type': 'object'}}),
            ('file_upload', 'INPUT', {'acceptedFormats': ['pdf'], 'maxFileSize': 1024}),
            (
                'agent',
                'MESSAGES',
                {'model': 'test', 'system_prompt': 'test'},
            ),
            (
                'rag_agent',
                'MESSAGES',
                {'agentId': 'test-agent', 'knowledgeBaseIds': ['kb-1']},
            ),
            ('llm_call', 'MESSAGES', {'model': 'test', 'template': 'test'}),
            ('structured_output', 'OUTPUT', {}),
            ('retrieve', 'FLOW', {'knowledgeBaseId': 'kb-1'}),
            ('document_extraction', 'FLOW', {}),
            ('human_review', 'FLOW', {}),
        ]
        for node_type, exec_mode, config in valid_types:
            node = NodeDefinition(type=node_type, execution_mode=exec_mode, config=config)
            assert node.type == node_type, f'Failed to create node of type {node_type}'
            assert node.execution_mode == exec_mode

    def test_all_valid_execution_modes(self):
        """Verify all 4 execution modes are accepted."""
        for mode in ('INPUT', 'OUTPUT', 'MESSAGES', 'FLOW'):
            node = NodeDefinition(
                type='plain_txt_input',
                execution_mode=mode,
                config={},
            )
            assert node.execution_mode == mode
