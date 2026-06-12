"""Routing parser that dispatches by file type to the right backend.

Most formats (PDF/DOCX/PPTX/images) need Docling's layout + OCR + VLM
pipeline. Spreadsheets do not — they are pure tabular data and parse far
faster and more reliably through MarkItDown (pandas/openpyxl) than through
Docling, which on prod fail-emptied them after burning a full 30-min
per-document slot.

``RoutingDocumentParser`` wraps a *primary* parser (typically the Docling
adapter) and a *tabular* parser (MarkItDown). For each document it inspects
the filename extension and delegates accordingly. The wrapper itself
satisfies ``DocumentParserPort`` so it is a drop-in replacement returned by
the factory — callers (backend + kb-ingest-pipeline) are unchanged.
"""

import logging
from typing import Any

from document_parsing.markitdown_adapter import (
    MarkItDownDocumentParser,
    is_markitdown_format,
)
from document_parsing.port import DocumentParserPort

logger = logging.getLogger(__name__)


class RoutingDocumentParser:
    """Dispatch documents to a tabular or primary parser by extension.

    Spreadsheets (``.xlsx``/``.xls``/``.csv``) go to ``tabular_parser``
    (MarkItDown); every other format goes to ``primary_parser`` (Docling).
    """

    def __init__(
        self,
        primary_parser: DocumentParserPort,
        tabular_parser: DocumentParserPort | None = None,
    ) -> None:
        """Construct the router.

        Args:
            primary_parser: Parser for non-tabular formats (e.g. the Docling
                adapter). All non-spreadsheet documents delegate here.
            tabular_parser: Parser for spreadsheets. Defaults to a fresh
                ``MarkItDownDocumentParser`` when not supplied.
        """
        self._primary = primary_parser
        self._tabular = tabular_parser or MarkItDownDocumentParser()

    def parse(
        self,
        file_content: bytes,
        filename: str,
    ) -> list[dict[str, Any]]:
        """Parse *file_content*, routing spreadsheets to MarkItDown.

        Args:
            file_content: Raw bytes of the document.
            filename: Original filename including extension; its suffix
                selects the backend.

        Returns:
            Normalized element dicts from whichever backend handled the file.
        """
        if is_markitdown_format(filename):
            logger.info('Routing %s to MarkItDown (tabular)', filename)
            return self._tabular.parse(file_content, filename)
        return self._primary.parse(file_content, filename)

    def health_check(self) -> dict[str, Any]:
        """Aggregate health of both wrapped parsers.

        Returns:
            A dict with an overall ``status`` (``healthy`` only when BOTH
            backends are healthy) plus per-parser detail under ``primary``
            and ``tabular``. Never raises.
        """
        primary = self._primary.health_check()
        tabular = self._tabular.health_check()
        overall = (
            'healthy'
            if primary.get('status') == 'healthy' and tabular.get('status') == 'healthy'
            else 'degraded'
        )
        return {'status': overall, 'primary': primary, 'tabular': tabular}
