"""DocTags-aware chunker built on docling-core's HybridChunker.

Reconstructs a ``DoclingDocument`` from a Docling DocTags string and applies
docling-core's hierarchy- and tokenization-aware ``HybridChunker``. The
output is the same *grouped-chunk dict* shape that ``ContextualChunker``'s
``group_elements_by_size`` / ``group_elements_hierarchically`` produce, so the
existing context-generation + embedding + ingestion tail
(``ContextualChunker.process_grouped_chunks``) runs downstream unchanged.

This module replaces only the *grouping* step when Docling DocTags are
available. Everything after grouping is reused as-is.

docling-core is imported lazily (inside ``__init__``) so importing this
module — or the ``document_parsing`` package — does not pull docling-core
unless a HybridChunker is actually constructed.

Version sensitivity:
    The docling-core APIs used here — ``DocTagsDocument.from_doctags_and_image_pairs``,
    ``DoclingDocument.load_from_doctags``, ``HybridChunker.chunk`` /
    ``.contextualize``, and ``DocChunk.meta`` (``headings`` / ``doc_items.prov``) —
    have shifted across docling-core releases. Confirm them against the pinned
    version (see ``pyproject.toml`` and infra Open Question #4) before relying
    on this in production, and prefer an explicit offline ``tokenizer`` to
    avoid a runtime model download in scale-to-zero ECS tasks.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default per-chunk token budget. Aligns with ContextualChunker's
# max_chunk_size (400-800) and stays under the Titan embed-text-v2 limit.
DEFAULT_MAX_TOKENS = 512


class HybridChunker:
    """Group Docling DocTags into ContextualChunker-compatible chunk dicts.

    Args:
        max_tokens: Maximum tokens per chunk. Set to the consuming pipeline's
            ``max_chunk_size`` so chunk sizes match the existing behavior and
            respect the embedding model's token limit.
        merge_peers: Whether docling-core should merge undersized sibling
            chunks that share metadata. Mirrors the upstream default.
        tokenizer: Optional explicit docling-core tokenizer (instance, or
            model name/path). Leave ``None`` to use docling-core's default,
            but note the default may download a HuggingFace tokenizer at
            runtime — pass an offline tokenizer in deployed environments.
    """

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        merge_peers: bool = True,
        tokenizer: Any = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.merge_peers = merge_peers

        from docling_core.transforms.chunker.hybrid_chunker import (
            HybridChunker as _DLHybridChunker,
        )

        chunker_kwargs: dict[str, Any] = {
            'max_tokens': max_tokens,
            'merge_peers': merge_peers,
        }
        if tokenizer is not None:
            chunker_kwargs['tokenizer'] = tokenizer
        self._dl = _DLHybridChunker(**chunker_kwargs)

    def chunk(
        self,
        elements: list[dict[str, Any]],
        doctags: str,
        document_name: str,
    ) -> list[dict[str, Any]]:
        """Chunk a document from its DocTags into grouped-chunk dicts.

        Args:
            elements: Unstructured-compatible elements from the same parse.
                Currently unused — HybridChunker derives structure from the
                authoritative DocTags hierarchy — but accepted so call sites
                can pass both the parser's ``elements`` and ``last_doctags``
                uniformly.
            doctags: Docling DocTags string (``parser.last_doctags``).
            document_name: Source document name, used for the reconstructed
                document and for downstream chunk IDs.

        Returns:
            A list of grouped-chunk dicts, each shaped as::

                {'elements': [{'type', 'text', 'metadata'}],
                 'tokens': int,
                 'metadata': {'section_title'?, 'page_number'?, 'chunk_type'}}

            compatible with ``ContextualChunker.process_grouped_chunks``.
            Returns ``[]`` when ``doctags`` is empty.
        """
        if not doctags:
            return []

        doc = self._load_docling_document(doctags, document_name)
        grouped: list[dict[str, Any]] = []
        for dl_chunk in self._dl.chunk(dl_doc=doc):
            text = self._dl.contextualize(chunk=dl_chunk)
            if not text or not text.strip():
                continue
            section_title = self._section_title(dl_chunk)
            page_number = self._first_page(dl_chunk)
            metadata = {
                k: v
                for k, v in {
                    'section_title': section_title,
                    'page_number': page_number,
                    'chunk_type': 'text',
                }.items()
                if v is not None
            }
            element = {
                'type': 'NarrativeText',
                'text': text,
                'metadata': {
                    'filename': document_name,
                    **({'page_number': page_number} if page_number is not None else {}),
                },
            }
            grouped.append(
                {
                    # Informational only: process_single_chunk recomputes the
                    # authoritative token count from the contextualized text.
                    'tokens': max(1, len(text) // 4),
                    'elements': [element],
                    'metadata': metadata,
                }
            )

        logger.info(
            'HybridChunker produced %d chunks from DocTags for %s',
            len(grouped),
            document_name,
        )
        return grouped

    @staticmethod
    def _load_docling_document(doctags: str, document_name: str) -> Any:
        """Reconstruct a DoclingDocument from a DocTags string.

        Isolated so the version-sensitive load API can be adjusted in one
        place once docling-core is pinned (infra Open Question #4).
        """
        from docling_core.types.doc.document import DoclingDocument, DocTagsDocument

        doctags_doc = DocTagsDocument.from_doctags_and_image_pairs([doctags], [None])
        return DoclingDocument.load_from_doctags(doctags_doc, document_name=document_name)

    @staticmethod
    def _section_title(dl_chunk: Any) -> str | None:
        """Join the chunk's heading path (``meta.headings``) into a title."""
        headings = getattr(getattr(dl_chunk, 'meta', None), 'headings', None)
        if not headings:
            return None
        return ' > '.join(str(h) for h in headings if h)

    @staticmethod
    def _first_page(dl_chunk: Any) -> int | None:
        """Return the first page number from the chunk's provenance, if any."""
        doc_items = getattr(getattr(dl_chunk, 'meta', None), 'doc_items', None) or []
        for item in doc_items:
            for prov in getattr(item, 'prov', None) or []:
                page_no = getattr(prov, 'page_no', None)
                if page_no is not None:
                    return page_no
        return None
