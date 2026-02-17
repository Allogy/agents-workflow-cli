"""
Workflow-related enums for the Agents Platform.

These enums are shared between the backend and CLI. They have zero external
dependencies beyond the Python standard library.
"""

from enum import Enum


class NodeConfigType(str, Enum):
    """Node configuration types based on conceptual architecture."""

    # Agent nodes
    AGENT = 'AGENT'
    RAG_AGENT = 'RAG_AGENT'

    # LLM and output nodes
    LLM_CALL = 'LLM_CALL'
    STRUCTURED_OUTPUT = 'STRUCTURED_OUTPUT'

    # Data retrieval
    RETRIEVE = 'RETRIEVE'

    # Input nodes
    PLAIN_TXT_INPUT = 'PLAIN_TXT_INPUT'
    STRUCTURED_INPUT = 'STRUCTURED_INPUT'
    FILE_UPLOAD = 'FILE_UPLOAD'

    # Human interaction
    HUMAN_REVIEW = 'HUMAN_REVIEW'

    SELECTION = 'SELECTION'
    ITERATOR = 'ITERATOR'
    DOCUMENT_EXTRACTION = 'DOCUMENT_EXTRACTION'


class ExecutionMode(str, Enum):
    """Execution mode for nodes - determines input/output handling."""

    INPUT = 'INPUT'
    OUTPUT = 'OUTPUT'
    MESSAGES = 'MESSAGES'
    FLOW = 'FLOW'


class EdgeType(str, Enum):
    """Edge types for connecting nodes."""

    STATIC = 'STATIC'
    CONDITIONAL = 'CONDITIONAL'
    METADATA = 'METADATA'
    RECURSIVE = 'RECURSIVE'
    MAPPING = 'MAPPING'


class StepExecutionType(str, Enum):
    """
    Step execution types for pydantic-graph nodes.

    Maps to pydantic-graph decorators:
    - STEP: Regular step decorated with @g.step
    - STREAM: Streaming step decorated with @g.stream (yields multiple values)
    - JOIN: Reducer/join node for collecting fan-out results
    - INPUT: Input nodes that merge frontend data into shared_state
    """

    STEP = 'STEP'
    STREAM = 'STREAM'
    JOIN = 'JOIN'
    INPUT = 'INPUT'


class ReducerType(str, Enum):
    """
    Reducer types for pydantic-graph join nodes.

    Maps to pydantic_graph.beta.join reducers:
    - REDUCE_NULL: Discards output, returns None (reduce_null)
    - REDUCE_LIST_APPEND: Collects outputs into a list (reduce_list_append)
    - REDUCE_DICT_MERGE: Merges dict outputs (reduce_dict_merge)
    - REDUCE_SUM: Sums numeric outputs
    - REDUCE_FIRST: Takes first non-None output
    - REDUCE_LAST: Takes last output
    - CUSTOM: Custom reducer function (stored in join_config.custom_reducer)
    """

    REDUCE_NULL = 'REDUCE_NULL'
    REDUCE_LIST_APPEND = 'REDUCE_LIST_APPEND'
    REDUCE_DICT_MERGE = 'REDUCE_DICT_MERGE'
    REDUCE_SUM = 'REDUCE_SUM'
    REDUCE_FIRST = 'REDUCE_FIRST'
    REDUCE_LAST = 'REDUCE_LAST'
    CUSTOM = 'CUSTOM'


class PathType(str, Enum):
    BEZIER = 'BEZIER'
    STRAIGHT = 'STRAIGHT'
    STEP = 'STEP'


class ExecutionStatus(str, Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'
    TIMEOUT = 'TIMEOUT'


class NodeExecutionStatus(str, Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'
