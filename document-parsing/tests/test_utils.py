"""Unit tests for services/document_parsing/utils.py.

Tests the shared file-size guard, text-bypass, and sanitization helpers
used by all document parsing adapters.
"""

import pytest

from document_parsing.utils import (
    AUDIO_EXTENSIONS,
    DIRECT_PARSE_EXTENSIONS,
    INGESTABLE_EXTENSIONS,
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_FORMATS,
    check_file_size,
    is_supported_format,
    sanitize_text,
    try_direct_parse,
)

pytestmark = pytest.mark.unit

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_max_file_size_is_50mb(self):
        assert MAX_FILE_SIZE_BYTES == 52_428_800

    def test_direct_parse_extensions_are_text_only(self):
        expected = {'.txt', '.md', '.markdown', '.csv'}
        assert expected == DIRECT_PARSE_EXTENSIONS

    def test_html_not_in_direct_parse(self):
        assert '.html' not in DIRECT_PARSE_EXTENSIONS
        assert '.htm' not in DIRECT_PARSE_EXTENSIONS


# ──────────────────────────────────────────────────────────────────────
# check_file_size
# ──────────────────────────────────────────────────────────────────────


class TestCheckFileSize:
    def test_small_file_passes(self):
        check_file_size(b'x' * 1024, 'small.pdf')

    def test_exactly_at_limit_passes(self):
        check_file_size(b'x' * MAX_FILE_SIZE_BYTES, 'exact.pdf')

    def test_one_byte_over_raises(self):
        with pytest.raises(ValueError, match='exceeds the 50 MB limit'):
            check_file_size(b'x' * (MAX_FILE_SIZE_BYTES + 1), 'big.pdf')

    def test_error_message_includes_filename(self):
        with pytest.raises(ValueError, match='myfile.pdf'):
            check_file_size(b'x' * (MAX_FILE_SIZE_BYTES + 1), 'myfile.pdf')

    def test_empty_file_passes(self):
        check_file_size(b'', 'empty.txt')


# ──────────────────────────────────────────────────────────────────────
# try_direct_parse
# ──────────────────────────────────────────────────────────────────────


class TestTryDirectParse:
    def test_txt_returns_elements(self):
        result = try_direct_parse(b'Hello world', 'readme.txt')
        assert result is not None
        assert len(result) == 1
        assert result[0]['type'] == 'NarrativeText'
        assert result[0]['text'] == 'Hello world'
        assert result[0]['metadata']['filename'] == 'readme.txt'

    def test_md_returns_elements(self):
        result = try_direct_parse(b'# Heading', 'notes.md')
        assert result is not None
        assert result[0]['text'] == '# Heading'

    def test_markdown_returns_elements(self):
        assert try_direct_parse(b'content', 'file.markdown') is not None

    def test_csv_returns_elements(self):
        result = try_direct_parse(b'name,value\nfoo,1', 'data.csv')
        assert result is not None
        assert 'name,value' in result[0]['text']

    def test_pdf_returns_none(self):
        assert try_direct_parse(b'%PDF-1.4', 'doc.pdf') is None

    def test_docx_returns_none(self):
        assert try_direct_parse(b'PK\x03\x04', 'doc.docx') is None

    def test_html_returns_none(self):
        assert try_direct_parse(b'<html></html>', 'page.html') is None

    def test_invalid_utf8_replaced_not_raised(self):
        result = try_direct_parse(b'\x80\x81 hello', 'broken.txt')
        assert result is not None
        assert 'hello' in result[0]['text']
        assert '�' in result[0]['text']


# ──────────────────────────────────────────────────────────────────────
# sanitize_text
# ──────────────────────────────────────────────────────────────────────


class TestSanitizeText:
    def test_strips_nul_bytes(self):
        assert sanitize_text('hello\x00world') == 'helloworld'

    def test_multiple_nul_bytes_removed(self):
        assert sanitize_text('\x00a\x00b\x00') == 'ab'

    def test_clean_string_unchanged(self):
        assert sanitize_text('no nul bytes here') == 'no nul bytes here'

    def test_empty_string_unchanged(self):
        assert sanitize_text('') == ''


# ──────────────────────────────────────────────────────────────────────
# Supported-format matrix (canonical source of truth)
# ──────────────────────────────────────────────────────────────────────


def test_supported_formats_matrix_tiers():
    """The matrix exposes exactly the documented capability tiers."""
    assert set(SUPPORTED_FORMATS.keys()) == {
        'rich',
        'markup',
        'tabular',
        'image',
        'audio',
    }


def test_supported_extensions_matches_image_matrix():
    """Every documented extension is present in the flat allow-list."""
    expected = {
        # rich
        '.pdf', '.docx', '.pptx',
        # markup
        '.md', '.markdown', '.html', '.htm', '.adoc', '.asciidoc', '.vtt',
        # tabular
        '.xlsx', '.csv',
        # image
        '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp',
        # audio
        '.mp3', '.wav',
    }
    assert SUPPORTED_EXTENSIONS == frozenset(expected)


def test_audio_extensions():
    assert AUDIO_EXTENSIONS == frozenset({'.mp3', '.wav'})


def test_ingestable_includes_text_native():
    """INGESTABLE = matrix + text-native; .txt must be ingestable."""
    assert '.txt' in INGESTABLE_EXTENSIONS
    assert SUPPORTED_EXTENSIONS <= INGESTABLE_EXTENSIONS
    assert DIRECT_PARSE_EXTENSIONS <= INGESTABLE_EXTENSIONS


@pytest.mark.parametrize(
    'filename',
    [
        'a.pdf', 'a.docx', 'a.pptx',
        'a.md', 'a.markdown', 'a.html', 'a.htm', 'a.adoc', 'a.asciidoc', 'a.vtt',
        'a.xlsx', 'a.csv',
        'a.png', 'a.jpg', 'a.jpeg', 'a.tif', 'a.tiff', 'a.bmp', 'a.webp',
        'a.mp3', 'a.wav',
        'a.txt',
        'UPPER.PDF', 'Mixed.DocX',
    ],
)
def test_is_supported_format_accepts(filename):
    assert is_supported_format(filename) is True


@pytest.mark.parametrize(
    'filename',
    ['a.doc', 'a.ppt', 'a.xls', 'a.rtf', 'a.odt', 'a.exe', 'a.zip', 'noext'],
)
def test_is_supported_format_rejects_legacy_and_unknown(filename):
    assert is_supported_format(filename) is False
