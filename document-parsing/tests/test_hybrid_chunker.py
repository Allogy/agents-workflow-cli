"""Unit tests for HybridChunker's DocTags -> grouped-chunk-dict mapping.

docling-core is stubbed via sys.modules so these tests exercise the mapping
logic (heading -> section_title, provenance -> page_number, output shape,
empty-input handling) without the real library, any network call, or a
tokenizer model download.
"""

import sys
import types
from types import SimpleNamespace

import pytest


def _make_chunk(text, headings=None, page=None):
    """Build a fake docling-core DocChunk-like object."""
    if page is not None:
        prov = [SimpleNamespace(page_no=page)]
        doc_items = [SimpleNamespace(prov=prov)]
    else:
        doc_items = []
    meta = SimpleNamespace(headings=headings, doc_items=doc_items)
    return SimpleNamespace(text=text, meta=meta)


@pytest.fixture
def fake_docling(monkeypatch):
    """Install a fake docling_core module tree; yields a mutable state dict.

    Set ``state['chunks']`` to control what the fake chunker yields.
    """
    state = {'chunks': [], 'init_kwargs': None}

    class _DLHybridChunker:
        def __init__(self, **kwargs):
            state['init_kwargs'] = kwargs

        def chunk(self, dl_doc=None):
            return iter(dl_doc.chunks)

        def contextualize(self, chunk=None):
            return chunk.text

    class _DoclingDocument:
        def __init__(self, chunks):
            self.chunks = chunks

        @staticmethod
        def load_from_doctags(doctags_doc, document_name='file'):
            return _DoclingDocument(state['chunks'])

    class _DocTagsDocument:
        @staticmethod
        def from_doctags_and_image_pairs(doctags, images):
            return ('doctags-document', doctags, images)

    hc_mod = types.ModuleType('docling_core.transforms.chunker.hybrid_chunker')
    hc_mod.HybridChunker = _DLHybridChunker
    doc_mod = types.ModuleType('docling_core.types.doc.document')
    doc_mod.DoclingDocument = _DoclingDocument
    doc_mod.DocTagsDocument = _DocTagsDocument

    modules = {
        'docling_core': types.ModuleType('docling_core'),
        'docling_core.transforms': types.ModuleType('docling_core.transforms'),
        'docling_core.transforms.chunker': types.ModuleType('docling_core.transforms.chunker'),
        'docling_core.transforms.chunker.hybrid_chunker': hc_mod,
        'docling_core.types': types.ModuleType('docling_core.types'),
        'docling_core.types.doc': types.ModuleType('docling_core.types.doc'),
        'docling_core.types.doc.document': doc_mod,
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return state


class TestHybridChunker:
    def test_empty_doctags_returns_empty(self, fake_docling):
        from document_parsing.hybrid_chunker import HybridChunker

        assert HybridChunker().chunk([], '', 'doc.pdf') == []

    def test_maps_text_headings_and_page(self, fake_docling):
        from document_parsing.hybrid_chunker import HybridChunker

        fake_docling['chunks'] = [
            _make_chunk('Intro paragraph.', headings=['Chapter 1', 'Overview'], page=3),
        ]
        grouped = HybridChunker(max_tokens=256).chunk([], '<doctags/>', 'doc.pdf')

        assert len(grouped) == 1
        chunk = grouped[0]
        assert chunk['metadata']['section_title'] == 'Chapter 1 > Overview'
        assert chunk['metadata']['page_number'] == 3
        assert chunk['metadata']['chunk_type'] == 'text'
        assert isinstance(chunk['tokens'], int) and chunk['tokens'] >= 1
        assert len(chunk['elements']) == 1
        element = chunk['elements'][0]
        assert element['type'] == 'NarrativeText'
        assert element['text'] == 'Intro paragraph.'
        assert element['metadata']['filename'] == 'doc.pdf'
        assert element['metadata']['page_number'] == 3

    def test_omits_missing_section_and_page(self, fake_docling):
        from document_parsing.hybrid_chunker import HybridChunker

        fake_docling['chunks'] = [_make_chunk('No headings here.')]
        grouped = HybridChunker().chunk([], '<doctags/>', 'doc.pdf')

        assert 'section_title' not in grouped[0]['metadata']
        assert 'page_number' not in grouped[0]['metadata']
        assert 'page_number' not in grouped[0]['elements'][0]['metadata']

    def test_skips_blank_chunks(self, fake_docling):
        from document_parsing.hybrid_chunker import HybridChunker

        fake_docling['chunks'] = [
            _make_chunk('   '),
            _make_chunk('Real content.'),
        ]
        grouped = HybridChunker().chunk([], '<doctags/>', 'doc.pdf')

        assert len(grouped) == 1
        assert grouped[0]['elements'][0]['text'] == 'Real content.'

    def test_max_tokens_passed_to_docling(self, fake_docling):
        from document_parsing.hybrid_chunker import HybridChunker

        HybridChunker(max_tokens=333, merge_peers=False)
        assert fake_docling['init_kwargs']['max_tokens'] == 333
        assert fake_docling['init_kwargs']['merge_peers'] is False
