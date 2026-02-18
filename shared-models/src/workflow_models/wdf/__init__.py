"""
Workflow Definition Format (WDF) models.

Pydantic v2 models for the YAML-based workflow definition format.
These models represent the human-readable, file-based representation
of workflows — distinct from the API-oriented schemas used for
REST operations.

Usage::

    from workflow_models.wdf import WorkflowDefinition, NodeDefinition, EdgeDefinition

Reference: RFC Section 4.1-4.3, Jira RAG-945.
"""

from workflow_models.wdf.edges import VALID_EDGE_TYPES, EdgeDefinition
from workflow_models.wdf.nodes import (
    VALID_EXECUTION_MODES,
    VALID_NODE_TYPES,
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
from workflow_models.wdf.validation import (
    ValidationResult,
    check_cycles,
    check_reachability,
    check_variable_references,
)
from workflow_models.wdf.variable_ref import VariableRef, extract_variable_refs
from workflow_models.wdf.workflow import WorkflowDefinition

__all__ = [
    # Top-level models
    'WorkflowDefinition',
    'NodeDefinition',
    'EdgeDefinition',
    # Node config schemas
    'PlainTxtInputConfig',
    'StructuredInputConfig',
    'FileUploadConfig',
    'AgentConfig',
    'RagAgentConfig',
    'LlmCallConfig',
    'StructuredOutputConfig',
    'RetrieveConfig',
    'DocumentExtractionConfig',
    'HumanReviewConfig',
    # Supporting types
    'ExtractionField',
    'VALID_NODE_TYPES',
    'VALID_EXECUTION_MODES',
    'VALID_EDGE_TYPES',
    # Variable reference utilities
    'VariableRef',
    'extract_variable_refs',
    # Validation utilities
    'ValidationResult',
    'check_reachability',
    'check_cycles',
    'check_variable_references',
]
