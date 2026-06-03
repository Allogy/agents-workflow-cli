"""Verify both adapters satisfy the DocumentParserPort Protocol.

Uses runtime_checkable to assert structural compatibility without
instantiating the adapters against live services.
"""

from typing import Any, Protocol, runtime_checkable

import pytest

from document_parsing.docling_adapter import DoclingServeDocumentParser
from document_parsing.unstructured_adapter import UnstructuredDocumentParser

pytestmark = pytest.mark.unit


# Make DocumentParserPort runtime-checkable for isinstance checks
@runtime_checkable
class _CheckableParserPort(Protocol):
    def parse(self, file_content: bytes, filename: str) -> list[dict[str, Any]]: ...
    def health_check(self) -> dict[str, Any]: ...


class TestDocumentParserPortContract:
    def test_unstructured_adapter_satisfies_protocol(self):
        parser = UnstructuredDocumentParser(url='http://localhost:8125')
        assert isinstance(parser, _CheckableParserPort)

    def test_docling_adapter_satisfies_protocol(self):
        parser = DoclingServeDocumentParser(url='http://localhost:5001')
        assert isinstance(parser, _CheckableParserPort)

    def test_unstructured_has_parse_method(self):
        parser = UnstructuredDocumentParser(url='http://localhost:8125')
        assert callable(getattr(parser, 'parse', None))

    def test_unstructured_has_health_check_method(self):
        parser = UnstructuredDocumentParser(url='http://localhost:8125')
        assert callable(getattr(parser, 'health_check', None))

    def test_docling_has_parse_method(self):
        parser = DoclingServeDocumentParser(url='http://localhost:5001')
        assert callable(getattr(parser, 'parse', None))

    def test_docling_has_health_check_method(self):
        parser = DoclingServeDocumentParser(url='http://localhost:5001')
        assert callable(getattr(parser, 'health_check', None))
