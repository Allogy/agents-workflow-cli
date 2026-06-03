"""Provider-agnostic helpers for document parsing.

Shared constants and utility functions used by all document parsing
adapters (Unstructured, Docling Serve, or any future backend).

Previously housed in ``services/unstructured_utils.py``; extracted here
to be provider-neutral and reusable across adapters.
"""

import logging
import pathlib
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# File-size guard
# ──────────────────────────────────────────────────────────────────────

# Maximum file size (in bytes) accepted by the document parsing pipeline.
# Files above this threshold are rejected to prevent OOM in parsing
# containers. Aligned with the upload endpoint limit in
# src/routers/knowledge_base.py.
MAX_FILE_SIZE_BYTES: int = 52_428_800  # 50 MB

# ──────────────────────────────────────────────────────────────────────
# Direct-parse (text bypass) extensions
# ──────────────────────────────────────────────────────────────────────

# Extensions that can be parsed directly as UTF-8 text without invoking
# any external parsing service. These formats contain no structural
# elements that would be lost by a plain-text read.
#
# Note: .html/.htm are intentionally EXCLUDED. Structured parsers
# preserve DOM structure (tables, lists) that plain text extraction
# loses. See TEXT_NATIVE_FORMATS for the superset used when DOM
# fidelity is not required (e.g. S3 raw-read for context reconstruction).
DIRECT_PARSE_EXTENSIONS: frozenset[str] = frozenset(
    {
        '.txt',
        '.md',
        '.markdown',
        '.csv',
    }
)

# ──────────────────────────────────────────────────────────────────────
# Text-native formats (readable as UTF-8 from S3)
# ──────────────────────────────────────────────────────────────────────

# Superset of DIRECT_PARSE_EXTENSIONS — includes .html/.htm because
# HTML can be decoded as UTF-8 text for context reconstruction even
# though it benefits from a structural parser for full fidelity.
TEXT_NATIVE_FORMATS: frozenset[str] = frozenset(
    {
        '.txt',
        '.md',
        '.markdown',
        '.csv',
        '.html',
        '.htm',
    }
)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def check_file_size(
    file_content: bytes,
    filename: str,
) -> None:
    """Raise ValueError if *file_content* exceeds the parsing size limit.

    Call this before sending any payload to an external parsing service
    to prevent OOM conditions in the parsing container.

    Args:
        file_content: Raw document bytes to validate.
        filename: Original filename, used only for the error message.

    Raises:
        ValueError: When ``len(file_content) > MAX_FILE_SIZE_BYTES``.
    """
    if len(file_content) > MAX_FILE_SIZE_BYTES:
        file_size_mb = len(file_content) / (1024 * 1024)
        max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise ValueError(
            f'File {filename} ({file_size_mb:.1f} MB) exceeds the '
            f'{max_mb:.0f} MB limit for document parsing. '
            f'Consider splitting the document or using a text-only strategy.'
        )


def try_direct_parse(
    file_content: bytes,
    filename: str,
) -> list[dict[str, Any]] | None:
    """Return element dicts if *filename* can be parsed as plain text.

    Bypasses all external parsing services for formats in
    ``DIRECT_PARSE_EXTENSIONS``. Returns ``None`` when the file type
    requires a structured parser.

    Args:
        file_content: Raw document bytes.
        filename: Original filename; suffix determines the parse path.

    Returns:
        A single-element list containing a NarrativeText dict when the
        extension is in ``DIRECT_PARSE_EXTENSIONS``, or ``None`` when
        external parsing is required.
    """
    ext = pathlib.Path(filename).suffix.lower()
    if ext not in DIRECT_PARSE_EXTENSIONS:
        return None
    logger.info(f'Bypassing external parser for text format {ext}: {filename}')
    text = file_content.decode('utf-8', errors='replace')
    return [{'type': 'NarrativeText', 'text': text, 'metadata': {'filename': filename}}]


def sanitize_text(text: str) -> str:
    """Strip NUL bytes that PostgreSQL TEXT columns reject.

    Args:
        text: Raw extracted text that may contain NUL characters.

    Returns:
        The same string with all ``\\x00`` characters removed.
    """
    return text.replace('\x00', '')
