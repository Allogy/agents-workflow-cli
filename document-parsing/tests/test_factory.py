"""Unit tests for the shared document_parsing factory.

Verifies create_document_parser(ParserConfig) selects the correct adapter
and threads the Docling VLM preset and page-batch size through.
"""

from document_parsing import (
    DoclingServeDocumentParser,
    ParserConfig,
    UnstructuredDocumentParser,
    create_document_parser,
)


class TestCreateDocumentParser:
    def test_returns_docling_adapter_when_docling_url_set(self):
        parser = create_document_parser(
            ParserConfig(
                docling_serve_url='http://docling-serve:5001',
                docling_vlm_pipeline_preset='bedrock-proxy',
                docling_page_batch_size=50,
                route_spreadsheets_to_markitdown=False,
            )
        )
        assert isinstance(parser, DoclingServeDocumentParser)
        assert parser.url == 'http://docling-serve:5001'
        assert parser.vlm_pipeline_preset == 'bedrock-proxy'
        assert parser.page_batch_size == 50

    def test_returns_unstructured_adapter_when_docling_url_empty(self):
        parser = create_document_parser(
            ParserConfig(
                docling_serve_url='',
                unstructured_api_url='http://unstructured:8125',
                route_spreadsheets_to_markitdown=False,
            )
        )
        assert isinstance(parser, UnstructuredDocumentParser)
        assert parser.url == 'http://unstructured:8125'

    def test_returns_unstructured_adapter_when_docling_url_none(self):
        parser = create_document_parser(
            ParserConfig(
                docling_serve_url=None,
                unstructured_api_url='http://unstructured:8125',
                route_spreadsheets_to_markitdown=False,
            )
        )
        assert isinstance(parser, UnstructuredDocumentParser)
