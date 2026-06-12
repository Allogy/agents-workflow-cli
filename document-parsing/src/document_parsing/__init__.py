"""Shared document parsing abstraction for the Capillary Actions platform.

A single, version-pinned home for the document-parsing port, its adapters
(Docling Serve and legacy Unstructured), the parser factory, and the
DocTags-aware ``HybridChunker``. Both the backend (``agents-runner``) and
the standalone ``kb-ingest-pipeline`` image import this package so the two
consumers can never drift apart.

Public API:
    - ``DocumentParserPort`` — adapter interface (Protocol).
    - ``ParserConfig`` — configuration value object the factory consumes.
    - ``create_document_parser(config)`` — returns the configured adapter.
    - ``DoclingServeDocumentParser`` / ``UnstructuredDocumentParser`` — adapters.
    - ``DoclingConversionError`` — terminal Docling conversion failure.
    - ``HybridChunker`` — DocTags-aware chunker (imported lazily; requires
      ``docling-core``).
"""

from document_parsing.config import ParserConfig
from document_parsing.docling_adapter import (
    DoclingConversionError,
    DoclingParseTimeout,
    DoclingServeDocumentParser,
)
from document_parsing.factory import create_document_parser
from document_parsing.markitdown_adapter import (
    MarkItDownConversionError,
    MarkItDownDocumentParser,
    is_markitdown_format,
)
from document_parsing.port import DocumentParserPort
from document_parsing.routing_adapter import RoutingDocumentParser
from document_parsing.unstructured_adapter import UnstructuredDocumentParser
from document_parsing.utils import (
    AUDIO_EXTENSIONS,
    INGESTABLE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_FORMATS,
    is_supported_format,
)

__all__ = [
    'DocumentParserPort',
    'ParserConfig',
    'create_document_parser',
    'DoclingServeDocumentParser',
    'UnstructuredDocumentParser',
    'MarkItDownDocumentParser',
    'RoutingDocumentParser',
    'MarkItDownConversionError',
    'is_markitdown_format',
    'DoclingConversionError',
    'DoclingParseTimeout',
    'HybridChunker',
    'SUPPORTED_FORMATS',
    'SUPPORTED_EXTENSIONS',
    'INGESTABLE_EXTENSIONS',
    'AUDIO_EXTENSIONS',
    'is_supported_format',
]


def __getattr__(name: str):
    """Lazily expose ``HybridChunker`` without importing ``docling-core`` eagerly.

    Keeps ``import document_parsing`` cheap for Unstructured-only consumers:
    the heavyweight ``docling-core`` dependency is only imported when
    ``document_parsing.HybridChunker`` is actually accessed.
    """
    if name == 'HybridChunker':
        from document_parsing.hybrid_chunker import HybridChunker

        return HybridChunker
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
