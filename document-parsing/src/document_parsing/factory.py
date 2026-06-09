"""Factory for selecting the active document parsing adapter.

Takes an explicit ``ParserConfig`` rather than reading any global
configuration, so the same factory serves both the backend (Pydantic
``Settings``) and the kb-ingest-pipeline (environment variables). Each
consumer maps its own configuration into a ``ParserConfig`` and delegates
here.

Adapters are imported lazily so that missing optional dependencies do not
cause import errors at startup — only at call time when the adapter is
actually needed.
"""

from document_parsing.config import ParserConfig
from document_parsing.port import DocumentParserPort


def create_document_parser(config: ParserConfig) -> DocumentParserPort:
    """Return the document parsing adapter selected by ``config``.

    - When ``config.docling_serve_url`` is non-empty, returns a
      ``DoclingServeDocumentParser`` pointed at that URL, threading
      ``docling_vlm_pipeline_preset`` and ``docling_page_batch_size``
      through to the adapter.
    - Otherwise returns an ``UnstructuredDocumentParser`` pointed at
      ``config.unstructured_api_url``.

    Args:
        config: Resolved parser configuration from the calling service.

    Returns:
        An object satisfying ``DocumentParserPort``.

    Raises:
        ImportError: If the selected adapter module is not installed.
    """
    if config.docling_serve_url:
        from document_parsing.docling_adapter import DoclingServeDocumentParser

        return DoclingServeDocumentParser(
            url=config.docling_serve_url,
            vlm_pipeline_preset=config.docling_vlm_pipeline_preset,
            page_batch_size=config.docling_page_batch_size,
            max_parse_seconds=config.docling_max_parse_seconds,
        )
    from document_parsing.unstructured_adapter import UnstructuredDocumentParser

    return UnstructuredDocumentParser(url=config.unstructured_api_url)
