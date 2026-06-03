"""Protocol definition for document parsing adapters.

Defines the DocumentParserPort interface that all document parsing
adapters must satisfy. Adapters include UnstructuredDocumentParser
(legacy) and DoclingServeDocumentParser (preferred).
"""

from typing import Any, Protocol


class DocumentParserPort(Protocol):
    """Interface for document parsing backends.

    Implementations must handle binary document content and return
    a normalized list of element dicts compatible with the Unstructured
    element schema.
    """

    def parse(
        self,
        file_content: bytes,
        filename: str,
    ) -> list[dict[str, Any]]:
        """Parse a document and return elements.

        Args:
            file_content: Raw bytes of the document to parse.
            filename: Original filename including extension, used to
                determine file type and populate metadata.

        Returns:
            List of element dicts, each containing:
              - type: str — element category (Title, NarrativeText,
                Table, Image, ListItem, Header, PageBreak)
              - text: str — extracted text content
              - metadata: dict — optional keys: text_as_html,
                image_base64, filetype, page_number, filename,
                source_document_url
        """
        ...

    def health_check(self) -> dict[str, Any]:
        """Check if the parsing service is reachable.

        Returns:
            Dict with at minimum a ``status`` key. Additional keys
            (e.g. ``url``, ``latency_ms``) are adapter-specific.

        Raises:
            Does not raise — returns a dict with an error key on failure
            so callers can aggregate health status without try/except.
        """
        ...
