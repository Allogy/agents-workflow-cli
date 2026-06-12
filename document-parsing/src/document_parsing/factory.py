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
      ``docling_vlm_pipeline_preset``, ``docling_page_batch_size``, and
      ``docling_page_batch_concurrency`` through to the adapter.
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

        primary: DocumentParserPort = DoclingServeDocumentParser(
            url=config.docling_serve_url,
            vlm_pipeline_preset=config.docling_vlm_pipeline_preset,
            page_batch_size=config.docling_page_batch_size,
            page_batch_concurrency=config.docling_page_batch_concurrency,
            max_parse_seconds=config.docling_max_parse_seconds,
        )
        return _maybe_route_spreadsheets(primary, config)
    from document_parsing.unstructured_adapter import UnstructuredDocumentParser

    return _maybe_route_spreadsheets(
        UnstructuredDocumentParser(url=config.unstructured_api_url), config
    )


def _maybe_route_spreadsheets(
    primary: DocumentParserPort,
    config: ParserConfig,
) -> DocumentParserPort:
    """Wrap *primary* in a spreadsheet-routing parser when enabled.

    When ``config.route_spreadsheets_to_markitdown`` is True, returns a
    ``RoutingDocumentParser`` that sends ``.xlsx``/``.xls``/``.csv`` to
    MarkItDown and everything else to *primary*. Otherwise returns *primary*
    unchanged. The MarkItDown adapter is imported lazily so consumers that
    never parse a spreadsheet do not pay for the dependency at import time.

    Args:
        primary: The non-tabular parser (Docling or Unstructured).
        config: Resolved parser configuration.

    Returns:
        Either a ``RoutingDocumentParser`` or *primary* itself.
    """
    if not config.route_spreadsheets_to_markitdown:
        return primary
    from document_parsing.routing_adapter import RoutingDocumentParser

    return RoutingDocumentParser(primary_parser=primary)
