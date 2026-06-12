"""MarkItDown-backed parser for spreadsheets and other tabular formats.

Spreadsheets (``.xlsx``/``.xls``/``.csv``) carry no layout/image content that
needs Docling's VLM + OCR pipeline — they are pure tabular data. Routing them
through Docling Serve is both wasteful and fragile: on prod the ``CSNH ... Rev
Trk`` workbooks blew past the 30-min per-document parse budget (Docling tried
to render embedded objects / un-renderable WMF images), so every spreadsheet
``DoclingParseTimeout``-ed and fail-emptied while still consuming a full slot
for 30 min (see APPS.md kb-orchestration Known Issues, 2026-06-11).

``MarkItDownDocumentParser`` extracts spreadsheets locally via Microsoft's
``markitdown`` (pandas/openpyxl under the hood) in milliseconds — no VLM, no
network, no per-document timeout risk. It returns elements in the same
normalized shape every other adapter produces (``type``/``text``/``metadata``
with ``text_as_html`` on tables), so the downstream HybridChunker is unchanged.

This adapter is intentionally narrow: it handles ONLY tabular formats. The
``RoutingDocumentParser`` dispatches spreadsheets here and delegates everything
else (PDF/DOCX/PPTX/images) to the primary Docling adapter.
"""

import logging
import pathlib
from io import BytesIO
from typing import Any

from document_parsing.docling_adapter import DoclingConversionError
from document_parsing.utils import sanitize_text

logger = logging.getLogger(__name__)

# Tabular formats handled by MarkItDown instead of Docling. ``.csv`` is
# included for completeness, but the runner's ``try_direct_parse`` already
# short-circuits ``.csv`` before any external/structured parser is reached;
# keeping it here makes the adapter correct even if called directly.
MARKITDOWN_EXTENSIONS: frozenset[str] = frozenset({'.xlsx', '.xls', '.csv'})


class MarkItDownConversionError(DoclingConversionError):
    """Terminal MarkItDown conversion failure for a tabular document.

    Subclasses ``DoclingConversionError`` so the ingest runner's existing
    terminal-error handling marks the document FAILED and continues to the
    next one (one bad spreadsheet never fails the whole KB), identical to how
    a terminal Docling failure is treated.
    """


def is_markitdown_format(filename: str) -> bool:
    """Return True when *filename* should be parsed by MarkItDown.

    Args:
        filename: Original filename or S3 key; only the suffix is examined.

    Returns:
        True when the lower-cased extension is a MarkItDown tabular format.
    """
    return pathlib.Path(filename).suffix.lower() in MARKITDOWN_EXTENSIONS


class MarkItDownDocumentParser:
    """Parse tabular documents (spreadsheets) via Microsoft MarkItDown.

    Satisfies ``DocumentParserPort``. Conversion is local, deterministic, and
    fast (no VLM/OCR, no network), so there is no poll loop and no per-document
    timeout — a spreadsheet that previously consumed a 30-min Docling slot now
    completes in well under a second.
    """

    def __init__(self) -> None:
        """Construct the parser, importing ``markitdown`` lazily at call time.

        The heavyweight import is deferred to ``parse`` so that importing this
        module (and the package factory) stays cheap for consumers that never
        touch a spreadsheet.
        """
        self._converter: Any | None = None

    def _get_converter(self) -> Any:
        """Return a cached ``MarkItDown`` converter, importing on first use.

        Raises:
            ImportError: If the ``markitdown`` package is not installed.
        """
        if self._converter is None:
            from markitdown import MarkItDown

            # enable_plugins=False keeps conversion deterministic and avoids
            # pulling optional third-party plugins into the parse path.
            self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    def parse(
        self,
        file_content: bytes,
        filename: str,
    ) -> list[dict[str, Any]]:
        """Parse a spreadsheet and return normalized element dicts.

        MarkItDown renders each worksheet to GitHub-flavored Markdown
        (tables as pipe tables). The full Markdown is returned as a single
        ``Table`` element so the chunker treats it as structured tabular
        content; ``metadata.text_as_html`` carries the same content for
        downstream consumers that prefer HTML.

        Args:
            file_content: Raw bytes of the spreadsheet.
            filename: Original filename including extension.

        Returns:
            A list with a single ``Table`` element dict. Empty list when the
            spreadsheet has no extractable content.

        Raises:
            MarkItDownConversionError: On a terminal conversion failure — the
                runner marks the doc FAILED and continues (does not fail the KB).
        """
        ext = pathlib.Path(filename).suffix.lower()
        try:
            converter = self._get_converter()
            # MarkItDown infers type from the stream; passing the extension
            # hint makes detection deterministic for ambiguous byte streams.
            result = converter.convert_stream(
                BytesIO(file_content),
                file_extension=ext,
            )
        except ImportError:
            # Missing dependency is an operational/config error, not a bad
            # document — re-raise so it surfaces loudly rather than silently
            # fail-emptying every spreadsheet.
            raise
        except Exception as exc:  # noqa: BLE001 — normalize to terminal error
            raise MarkItDownConversionError(
                f'MarkItDown failed to convert {filename}: {exc}'
            ) from exc

        markdown = sanitize_text((result.text_content or '').strip())
        if not markdown:
            logger.warning('MarkItDown produced no content for %s', filename)
            return []

        logger.info(
            'MarkItDown parsed %s: %d chars of markdown (no Docling/VLM)',
            filename,
            len(markdown),
        )
        return [
            {
                'type': 'Table',
                'text': markdown,
                'metadata': {
                    'filename': filename,
                    'text_as_html': markdown,
                    'parser': 'markitdown',
                },
            }
        ]

    def health_check(self) -> dict[str, Any]:
        """Report whether the ``markitdown`` dependency is importable.

        Returns:
            ``{'status': 'healthy', 'parser': 'markitdown'}`` when importable,
            otherwise ``{'status': 'unhealthy', 'error': ...}``. Never raises.
        """
        try:
            self._get_converter()
        except Exception as exc:  # noqa: BLE001 — health checks never raise
            return {'status': 'unhealthy', 'parser': 'markitdown', 'error': str(exc)}
        return {'status': 'healthy', 'parser': 'markitdown'}
