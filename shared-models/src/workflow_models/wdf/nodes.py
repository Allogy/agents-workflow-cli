"""WDF node config schemas for all 10 node types.

Each node type has a dedicated Pydantic v2 config model that validates
the config block in a .workflow.yaml file. NodeDefinition wraps the
type string + label + config dict, dispatching validation to the
appropriate config schema based on the declared type.

Config field names follow a hybrid approach: meaningful runtime fields
are drawn from both the backend ``parameters`` and ``config`` objects,
omitting frontend-only UI state (collapsed, validationLevel, etc.).

Reference: RFC Section 4.2, Jira RAG-945.
See also: backend/scripts/workflow_complete_tests/payloads/ for the
canonical JSON shapes.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Valid execution modes — kept as strings so shared-models stays enum-agnostic
# at the field level (callers can use the ExecutionMode enum values).
VALID_EXECUTION_MODES = {'INPUT', 'OUTPUT', 'MESSAGES', 'FLOW'}

# ============================================
# EXTRACTION FIELD (used by DocumentExtractionConfig)
# ============================================


class ExtractionField(BaseModel):
    """A single field to extract from a document."""

    name: str
    type: str
    required: bool = False


# ============================================
# INPUT NODE CONFIGS
# ============================================


class PlainTxtInputConfig(BaseModel):
    """Config for PLAIN_TXT_INPUT nodes. All fields optional."""

    placeholder: str | None = None


class StructuredInputConfig(BaseModel):
    """Config for STRUCTURED_INPUT nodes. Requires a JSON Schema object."""

    schema_: dict[str, Any] = Field(..., alias='schema')

    model_config = {'populate_by_name': True}


class FileUploadConfig(BaseModel):
    """Config for FILE_UPLOAD nodes.

    Fields drawn from backend parameters: acceptedFormats, maxFileSize,
    textExtraction, extractTables, preserveFormatting.
    """

    acceptedFormats: list[str]
    maxFileSize: int = Field(..., gt=0)
    textExtraction: str | None = None
    extractTables: bool | None = None
    preserveFormatting: bool | None = None


# ============================================
# PROCESSING NODE CONFIGS
# ============================================


class AgentConfig(BaseModel):
    """Config for AGENT nodes.

    Hybrid of backend ``config`` (runtime: model_name, system_prompt,
    temperature, max_tokens, tools) and ``parameters`` (agentId for
    linking to a registered agent). For WDF we use the user-facing
    field names: ``model`` instead of ``model_name``, ``maxTokens``
    instead of ``max_tokens``.
    """

    model: str
    system_prompt: str
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, gt=0)
    tools: list[Any] | None = None
    agentId: str | None = None


class RagAgentConfig(BaseModel):
    """Config for RAG_AGENT nodes.

    Uses backend parameters: agentId, knowledgeBaseIds (knowledge bases
    to query), primaryInput (variable reference for input routing), and
    disableRAG (flag to bypass RAG retrieval while keeping the agent).

    After ``workflow pull``, UUID-based fields are replaced with
    human-readable name variants (``agent_name``, ``knowledge_base_names``).
    The push command resolves names back to UUIDs before sending to the API.
    Validation accepts either form: UUID-based **or** name-based.
    """

    agentId: str | None = None
    knowledgeBaseIds: list[str] | None = None
    agent_name: str | None = None
    knowledge_base_names: list[str] | None = None
    primaryInput: str | None = None
    disableRAG: bool | None = Field(
        default=None,
        description='When true, bypasses RAG retrieval while keeping the agent.',
    )

    @model_validator(mode='after')
    def check_agent_reference(self) -> 'RagAgentConfig':
        """Ensure either agentId or agent_name is provided."""
        if not self.agentId and not self.agent_name:
            raise ValueError('RAG_AGENT config requires either agentId (UUID) or agent_name')
        return self

    @model_validator(mode='after')
    def check_kb_reference(self) -> 'RagAgentConfig':
        """Ensure either knowledgeBaseIds or knowledge_base_names is provided."""
        if not self.knowledgeBaseIds and not self.knowledge_base_names:
            raise ValueError(
                'RAG_AGENT config requires either knowledgeBaseIds (UUIDs) or knowledge_base_names'
            )
        return self


class LlmCallConfig(BaseModel):
    """Config for LLM_CALL nodes.

    Drawn from backend parameters: model, template (with variable refs),
    system_prompt, temperature, maxTokens, topP.
    """

    model: str
    template: str
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, gt=0)
    topP: float | None = Field(default=None, ge=0.0, le=1.0)


class StructuredOutputConfig(BaseModel):
    """Config for STRUCTURED_OUTPUT nodes.

    Backend ``config`` uses ``schema`` (JSON Schema). Also supports
    ``model`` for LLM-based output generation.
    """

    schema_: dict[str, Any] | None = Field(default=None, alias='schema')
    model: str | None = None

    model_config = {'populate_by_name': True}


# ============================================
# FLOW NODE CONFIGS
# ============================================


class RetrieveConfig(BaseModel):
    """Config for RETRIEVE nodes.

    Hybrid of backend ``parameters`` (knowledgeBaseId, topK, searchQuery) and
    ``config`` (enable_reranking, include_metadata, metadata_filters).

    After ``workflow pull``, UUID-based ``knowledgeBaseId`` is replaced with
    ``knowledge_base_name`` (singular) or ``knowledge_base_names`` (list).
    Validation accepts either form.
    """

    knowledgeBaseId: str | None = None
    knowledge_base_name: str | None = None
    knowledge_base_names: list[str] | None = None
    topK: int | None = Field(default=None, gt=0)
    searchQuery: str | None = None
    scoreThreshold: float | None = Field(default=None, ge=0.0, le=1.0)
    enableReranking: bool | None = None
    includeMetadata: bool | None = None

    @model_validator(mode='after')
    def check_kb_reference(self) -> 'RetrieveConfig':
        """Ensure at least one KB reference is provided."""
        if (
            not self.knowledgeBaseId
            and not self.knowledge_base_name
            and not self.knowledge_base_names
        ):
            raise ValueError(
                'RETRIEVE config requires knowledgeBaseId, '
                'knowledge_base_name, or knowledge_base_names'
            )
        return self


class DocumentExtractionConfig(BaseModel):
    """Config for DOCUMENT_EXTRACTION nodes.

    Keeps the WDF-native ``fields`` list for structured extraction.
    Adds ``extractTables`` and ``extractImages`` from backend parameters.
    """

    fields: list[ExtractionField] = Field(default_factory=list)
    extractionMethod: str | None = None
    prompt: str | None = None
    extractTables: bool | None = None
    extractImages: bool | None = None


# ============================================
# OUTPUT / HUMAN INTERACTION NODE CONFIGS
# ============================================


class HumanReviewConfig(BaseModel):
    """Config for HUMAN_REVIEW nodes.

    Aligned with backend ``config``: review_prompt, allow_approve,
    allow_reject, allow_edit. Also keeps WDF-native timeoutMinutes.
    """

    review_prompt: str | None = None
    timeoutMinutes: int | None = Field(default=None, gt=0)
    allowApprove: bool | None = None
    allowReject: bool | None = None
    allowEdit: bool | None = None


# ============================================
# NODE TYPE -> CONFIG MAPPING
# ============================================

# Maps YAML node type strings to their config model classes.
NODE_TYPE_CONFIG_MAP: dict[str, type[BaseModel]] = {
    'plain_txt_input': PlainTxtInputConfig,
    'structured_input': StructuredInputConfig,
    'file_upload': FileUploadConfig,
    'agent': AgentConfig,
    'rag_agent': RagAgentConfig,
    'llm_call': LlmCallConfig,
    'structured_output': StructuredOutputConfig,
    'retrieve': RetrieveConfig,
    'document_extraction': DocumentExtractionConfig,
    'human_review': HumanReviewConfig,
}

# All valid WDF node type strings.
VALID_NODE_TYPES = set(NODE_TYPE_CONFIG_MAP.keys())


# ============================================
# NODE DEFINITION (wrapper)
# ============================================


class NodeDefinition(BaseModel):
    """A node in a workflow definition file.

    Wraps the node type, execution mode, optional label, and config dict.
    The config is validated against the appropriate schema based on the
    declared type.
    """

    type: str
    execution_mode: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    # Populated after validation — the parsed, typed config object.
    parsed_config: BaseModel | None = Field(default=None, exclude=True)

    @field_validator('type')
    @classmethod
    def validate_node_type(cls, v: str) -> str:
        if v not in VALID_NODE_TYPES:
            raise ValueError(
                f'Unknown node type: {v!r}. Valid types: {", ".join(sorted(VALID_NODE_TYPES))}'
            )
        return v

    @field_validator('execution_mode')
    @classmethod
    def validate_execution_mode(cls, v: str) -> str:
        if v not in VALID_EXECUTION_MODES:
            raise ValueError(
                f'Unknown execution_mode: {v!r}. '
                f'Valid modes: {", ".join(sorted(VALID_EXECUTION_MODES))}'
            )
        return v

    @model_validator(mode='after')
    def validate_config_for_type(self) -> 'NodeDefinition':
        """Parse and validate the config dict against the node type's schema."""
        config_cls = NODE_TYPE_CONFIG_MAP.get(self.type)
        if config_cls is not None:
            self.parsed_config = config_cls.model_validate(self.config)
        return self
