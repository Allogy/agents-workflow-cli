"""Unit tests for the MarkItDown spreadsheet adapter and routing parser."""

import io

import pytest
from openpyxl import Workbook

from document_parsing.config import ParserConfig
from document_parsing.factory import create_document_parser
from document_parsing.markitdown_adapter import (
    MarkItDownConversionError,
    MarkItDownDocumentParser,
    is_markitdown_format,
)
from document_parsing.routing_adapter import RoutingDocumentParser


def _build_xlsx() -> bytes:
    """Return bytes of a tiny single-sheet workbook."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Rev Trk'
    ws.append(['Month', 'AtNeed', 'PreNeed'])
    ws.append(['Jan', 1200, 800])
    ws.append(['Feb', 1500, 950])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _StubParser:
    """Primary-parser stub that records whether it was called."""

    def __init__(self) -> None:
        self.called_with: str | None = None

    def parse(self, file_content: bytes, filename: str) -> list[dict]:
        self.called_with = filename
        return [{'type': 'NarrativeText', 'text': 'docling', 'metadata': {}}]

    def health_check(self) -> dict:
        return {'status': 'healthy', 'parser': 'stub'}


@pytest.mark.unit
@pytest.mark.parametrize(
    ('filename', 'expected'),
    [
        ('CSNH Rev Trk.xlsx', True),
        ('legacy.xls', True),
        ('data.csv', True),
        ('REPORT.XLSX', True),
        ('doc.pdf', False),
        ('slides.pptx', False),
        ('image.png', False),
    ],
)
def test_is_markitdown_format(filename: str, expected: bool) -> None:
    assert is_markitdown_format(filename) is expected


@pytest.mark.unit
def test_markitdown_parses_xlsx_to_table_element() -> None:
    parser = MarkItDownDocumentParser()
    elements = parser.parse(_build_xlsx(), 'CSNH Rev Trk.xlsx')

    assert len(elements) == 1
    el = elements[0]
    assert el['type'] == 'Table'
    assert el['metadata']['parser'] == 'markitdown'
    assert el['metadata']['filename'] == 'CSNH Rev Trk.xlsx'
    # text_as_html mirrors the markdown content for downstream consumers
    assert el['metadata']['text_as_html'] == el['text']
    # data made it through
    assert 'Month' in el['text'] and 'AtNeed' in el['text'] and '1200' in el['text']


@pytest.mark.unit
def test_markitdown_health_check_healthy() -> None:
    assert MarkItDownDocumentParser().health_check()['status'] == 'healthy'


@pytest.mark.unit
def test_markitdown_garbage_bytes_degrade_gracefully() -> None:
    """Non-spreadsheet bytes with a tabular extension must not crash.

    MarkItDown content-sniffs and falls back to plain text rather than
    raising, so the adapter returns whatever it extracted (possibly empty)
    instead of fail-emptying the whole document on a hard error.
    """
    parser = MarkItDownDocumentParser()
    # Must not raise; returns 0 or 1 element depending on sniffed content.
    elements = parser.parse(b'not a real spreadsheet at all', 'broken.xlsx')
    assert isinstance(elements, list)


@pytest.mark.unit
def test_markitdown_converter_exception_becomes_terminal_error() -> None:
    """A converter that raises is normalized to MarkItDownConversionError.

    This is the terminal-but-non-KB-failing path: the runner marks the doc
    FAILED and continues, exactly like a terminal Docling failure.
    """

    class _BoomConverter:
        def convert_stream(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError('xlrd could not open this legacy .xls')

    parser = MarkItDownDocumentParser()
    parser._converter = _BoomConverter()  # inject a converter that raises
    with pytest.raises(MarkItDownConversionError):
        parser.parse(b'\xd0\xcf\x11\xe0', 'legacy.xls')


@pytest.mark.unit
def test_router_sends_spreadsheets_to_markitdown_not_primary() -> None:
    stub = _StubParser()
    router = RoutingDocumentParser(primary_parser=stub)

    elements = router.parse(_build_xlsx(), 'CSNH Rev Trk.xlsx')

    # primary (Docling stub) must NOT have been called for a spreadsheet
    assert stub.called_with is None
    assert elements[0]['metadata']['parser'] == 'markitdown'


@pytest.mark.unit
def test_router_delegates_non_spreadsheets_to_primary() -> None:
    stub = _StubParser()
    router = RoutingDocumentParser(primary_parser=stub)

    elements = router.parse(b'%PDF-1.7 ...', 'report.pdf')

    assert stub.called_with == 'report.pdf'
    assert elements[0]['text'] == 'docling'


@pytest.mark.unit
def test_factory_wraps_when_routing_enabled() -> None:
    cfg = ParserConfig(
        docling_serve_url='http://docling:5001',
        route_spreadsheets_to_markitdown=True,
    )
    assert isinstance(create_document_parser(cfg), RoutingDocumentParser)


@pytest.mark.unit
def test_factory_no_wrap_when_routing_disabled() -> None:
    cfg = ParserConfig(
        docling_serve_url='http://docling:5001',
        route_spreadsheets_to_markitdown=False,
    )
    assert not isinstance(create_document_parser(cfg), RoutingDocumentParser)
