"""WDF node config schemas for all 10 node types.

Each node type has a dedicated Pydantic v2 config model that validates
the config block in a .workflow.yaml file. NodeDefinition wraps the
type string + label + config dict, dispatching validation to the
appropriate config schema based on the declared type.

Reference: RFC Section 4.2, Jira RAG-945.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

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
    """Config for FILE_UPLOAD nodes."""

    acceptedFormats: list[str]
    maxFileSize: int = Field(..., gt=0)
    textExtraction: str | None = None
    extractTables: bool | None = None
    preserveFormatting: bool | None = None


# ============================================
# PROCESSING NODE CONFIGS
# ============================================


class AgentConfig(BaseModel):
    """Config for AGENT nodes."""

    agentId: str
    agentConfig: dict[str, Any] | None = None


class RagAgentConfig(BaseModel):
    """Config for RAG_AGENT nodes."""

    agentId: str
    knowledgeBaseIds: list[str]
    knowledgeBasesOverride: bool | None = None


class LlmCallConfig(BaseModel):
    """Config for LLM_CALL nodes."""

    model: str
    template: str
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, gt=0)
    topP: float | None = Field(default=None, ge=0.0, le=1.0)


class StructuredOutputConfig(BaseModel):
    """Config for STRUCTURED_OUTPUT nodes. All fields optional."""

    model: str | None = None
    outputSchema: dict[str, Any] | None = None


# ============================================
# FLOW NODE CONFIGS
# ============================================


class RetrieveConfig(BaseModel):
    """Config for RETRIEVE nodes."""

    knowledgeBaseId: str
    topK: int | None = Field(default=None, gt=0)
    scoreThreshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DocumentExtractionConfig(BaseModel):
    """Config for DOCUMENT_EXTRACTION nodes."""

    fields: list[ExtractionField] = Field(..., min_length=1)
    extractionMethod: str | None = None
    prompt: str | None = None


# ============================================
# OUTPUT / HUMAN INTERACTION NODE CONFIGS
# ============================================


class HumanReviewConfig(BaseModel):
    """Config for HUMAN_REVIEW nodes."""

    instructions: str | None = None
    timeoutMinutes: int | None = Field(default=None, gt=0)


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

    Wraps the node type, optional label, and config dict. The config
    is validated against the appropriate schema based on the declared type.
    """

    type: str
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

    @model_validator(mode='after')
    def validate_config_for_type(self) -> 'NodeDefinition':
        """Parse and validate the config dict against the node type's schema."""
        config_cls = NODE_TYPE_CONFIG_MAP.get(self.type)
        if config_cls is not None:
            self.parsed_config = config_cls.model_validate(self.config)
        return self
