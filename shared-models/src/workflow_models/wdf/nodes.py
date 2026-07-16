"""WDF node config schemas for all 12 node types.

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

# Dead fields that have been removed from the WDF schema.
# Centralized list so push/pull/validation all share the same source of truth.
DEAD_FIELDS: dict[str, str] = {
    'topP': 'LLM_CALL nodes no longer support topP. Remove this field.',
    'extractionPrompt': 'STRUCTURED_OUTPUT nodes no longer support extractionPrompt. Remove this field.',
    'disableRAG': 'RAG_AGENT nodes no longer support disableRAG. Remove this field.',
    'textExtraction': 'FILE_UPLOAD nodes no longer support textExtraction. Remove this field.',
    'extractTables': 'FILE_UPLOAD nodes no longer support extractTables. Remove this field.',
    'preserveFormatting': 'FILE_UPLOAD nodes no longer support preserveFormatting. Remove this field.',
}


def _check_dead_fields(data: dict, dead_fields: set[str]) -> None:
    """Reject removed WDF fields with a clear error message."""
    if not isinstance(data, dict):
        return
    found = dead_fields & set(data.keys())
    if found:
        messages = []
        for field_name in sorted(found):
            messages.append(DEAD_FIELDS.get(field_name, f'{field_name} is no longer supported.'))
        raise ValueError(f'Removed field(s): {", ".join(sorted(found))}. ' + ' '.join(messages))


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

    Fields drawn from backend parameters: acceptedFormats, maxFileSize, saveToMemory.
    Removed: textExtraction, extractTables, preserveFormatting (dead fields).
    """

    acceptedFormats: list[str]
    maxFileSize: int = Field(..., gt=0)
    saveToMemory: bool = False

    @model_validator(mode='before')
    @classmethod
    def reject_dead_fields(cls, data: dict) -> dict:
        _check_dead_fields(data, {'textExtraction', 'extractTables', 'preserveFormatting'})
        return data


class MemoryFileUrlConfig(BaseModel):
    """Config for MEMORY_FILE_URL nodes.

    Produces a Document-Links-style download URL for a file the upstream
    pipeline wrote into the RLM agent memory bucket. ``path`` is relative
    to the org's memory root (e.g. ``outputs/report.pdf``) and may be a
    template variable (``{{slug.output.filename}}``).
    """

    path: str = Field(..., min_length=1)

    @field_validator('path')
    @classmethod
    def validate_path(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('path must be non-empty')
        # Reject absolute paths and upward traversal. The backend re-checks
        # after template resolution, but failing fast in CLI validation is
        # friendlier to workflow authors.
        if v.startswith('/'):
            raise ValueError(f'path must be relative (got absolute path: {v!r})')
        parts = v.split('/')
        if '..' in parts:
            raise ValueError(f"path must not contain '..' segments (got: {v!r})")
        return v


# ============================================
# PROCESSING NODE CONFIGS
# ============================================


class AgentConfig(BaseModel):
    """Config for AGENT nodes.

    Agent nodes delegate to a registered agent entity (referenced by
    ``agent_name`` or ``agentId``).  The agent carries its own system
    prompt, so the WDF config only needs routing (``primaryInput``) and
    optional runtime overrides (model, temperature, etc.).

    For WDF we use the user-facing field names: ``model`` instead of
    ``model_name``, ``maxTokens`` instead of ``max_tokens``.
    """

    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, gt=0)
    tools: list[Any] | None = None
    agentId: str | None = None
    primaryInput: str | None = None
    system_prompt: str | None = None
    use_rlm: bool | None = None
    web_tools_enabled: bool | None = None
    max_iterations: int | None = Field(
        default=None,
        gt=0,
        le=100,
        description='Max RLM iterations per agent node execution (default: 20, max: 100)',
    )


class RagAgentConfig(BaseModel):
    """Config for RAG_AGENT nodes.

    Uses backend parameters: agentId, knowledgeBaseIds (knowledge bases
    to query), primaryInput (variable reference for input routing).
    Removed: disableRAG (dead field).

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
    topK: int | None = Field(default=None, gt=0)
    system_prompt: str | None = None

    @model_validator(mode='before')
    @classmethod
    def reject_dead_fields(cls, data: dict) -> dict:
        _check_dead_fields(data, {'disableRAG'})
        return data

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
    system_prompt, temperature, maxTokens.
    Removed: topP (dead field).
    """

    model: str
    template: str
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, gt=0)

    @model_validator(mode='before')
    @classmethod
    def reject_dead_fields(cls, data: dict) -> dict:
        _check_dead_fields(data, {'topP'})
        return data


class StructuredOutputConfig(BaseModel):
    """Config for STRUCTURED_OUTPUT nodes.

    Backend config uses schema (JSON Schema). Also supports model for LLM-based output.
    Removed: extractionPrompt (dead field).
    Changed: schema is now required (was optional).
    """

    schema_: dict[str, Any] = Field(..., alias='schema')
    model: str | None = None
    primaryInput: str | None = None
    system_prompt: str | None = None

    model_config = {'populate_by_name': True}

    @model_validator(mode='before')
    @classmethod
    def reject_dead_fields(cls, data: dict) -> dict:
        _check_dead_fields(data, {'extractionPrompt'})
        return data


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
# INTEGRATION NODE CONFIGS
# ============================================


class ApiResponseFieldMapping(BaseModel):
    """A single response-body field to expose as a named output variable.

    ``jsonPath`` is a glom path into the parsed JSON response
    (e.g. ``types[0].type.name``, ``data.results[0].id``, ``name``) and
    ``variable`` is the output name it is bound to, referenced downstream as
    ``{{node.output.<variable>}}``.
    """

    variable: str
    jsonPath: str


class ApiConsumptionConfig(BaseModel):
    """Config for API_CONSUMPTION nodes.

    Calls an external HTTP API via a configured org-scoped API Connector.
    The connector (referenced by ``connectorId``) carries the OpenAPI schema,
    variable definitions, host allowlist and secret bundle on the backend;
    the WDF config only needs the connector reference plus routing/limits.

    When ``saveToMemory`` is true, the HTTP response body is streamed to a
    file in the workflow run's memory scope instead of being parsed inline,
    exposing downstream output variables ``output.memory_file_path``,
    ``output.memory_file_url``, ``output.content_type``, ``output.size_bytes``
    and ``output.status_code``.
    """

    connectorId: str
    primaryInput: str | None = None
    maxRecursionDepth: int | None = 1
    operationHint: str | None = None
    timeoutSeconds: int | None = None
    # When true, stream the HTTP response body to a file in the run memory
    # scope rather than parsing it inline. Mirrors FileUploadConfig.saveToMemory.
    saveToMemory: bool = False
    # Templated, path-confined relative path under the run memory scope
    # (e.g. ``transcripts/{{zoom_trigger.meeting_uuid}}.vtt``). Only used when
    # saveToMemory is true. Defaults to ``api/{node_id}/response.<ext>`` on the
    # backend when omitted.
    memoryFilePath: str | None = None
    # Extract JSON paths from the response body and expose them as named
    # workflow output variables. Each entry binds a glom ``jsonPath`` into the
    # parsed JSON response to an output ``variable`` referenced downstream as
    # ``{{node.output.<variable>}}``.
    responseVariableMappings: list[ApiResponseFieldMapping] = Field(default_factory=list)
    # Templated per-request HTTP headers (e.g. ``authorization: 'Bearer
    # {{get_token.output.access_token}}'``). Merged by the executor with the
    # connector's auth headers, where connector auth wins on collision.
    headers: dict[str, str] | None = None
    # Templated query/call parameters (e.g. a ``from``/``to`` date window)
    # forwarded to the planner.
    callParams: dict[str, str] | None = None


# ============================================
# NODE TYPE -> CONFIG MAPPING
# ============================================

# Maps YAML node type strings to their config model classes.
NODE_TYPE_CONFIG_MAP: dict[str, type[BaseModel]] = {
    'plain_txt_input': PlainTxtInputConfig,
    'structured_input': StructuredInputConfig,
    'file_upload': FileUploadConfig,
    'memory_file_url': MemoryFileUrlConfig,
    'agent': AgentConfig,
    'rag_agent': RagAgentConfig,
    'llm_call': LlmCallConfig,
    'structured_output': StructuredOutputConfig,
    'retrieve': RetrieveConfig,
    'document_extraction': DocumentExtractionConfig,
    'human_review': HumanReviewConfig,
    'api_consumption': ApiConsumptionConfig,
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
    timeout_seconds: int | None = Field(default=None, gt=0)

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
