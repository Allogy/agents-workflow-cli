"""Unit tests for services/document_parsing/unstructured_adapter.py."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from document_parsing.unstructured_adapter import UnstructuredDocumentParser

pytestmark = pytest.mark.unit

BASE_URL = 'http://unstructured:8125'


class TestUnstructuredDocumentParserParse:
    def test_raises_value_error_for_oversized_file(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        huge = b'x' * (52_428_800 + 1)
        with pytest.raises(ValueError, match='exceeds the 50 MB limit'):
            parser.parse(huge, 'big.pdf')

    def test_text_file_bypasses_http(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        with patch('requests.post') as mock_post:
            result = parser.parse(b'hello world', 'readme.txt')
        mock_post.assert_not_called()
        assert len(result) == 1
        assert result[0]['type'] == 'NarrativeText'

    def test_pdf_calls_unstructured_api(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        mock_response = MagicMock()
        mock_response.json.return_value = [{'type': 'Title', 'text': 'Hello'}]
        mock_response.raise_for_status.return_value = None
        with patch('requests.post', return_value=mock_response) as mock_post:
            result = parser.parse(b'%PDF-1.4', 'doc.pdf')
        mock_post.assert_called_once()
        assert result == [{'type': 'Title', 'text': 'Hello'}]

    def test_4xx_error_is_not_retried(self):
        parser = UnstructuredDocumentParser(url=BASE_URL, max_retries=3)
        mock_response = MagicMock()
        mock_response.status_code = 400
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        with (
            patch('requests.post', return_value=mock_response),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')

    def test_connection_error_retried_then_raises_runtime_error(self):
        parser = UnstructuredDocumentParser(url=BASE_URL, max_retries=2)
        with (
            patch('requests.post', side_effect=requests.exceptions.ConnectionError('refused')),
            patch.object(parser, '_wait_for_healthy'),
            patch('time.sleep'),
            pytest.raises(RuntimeError, match='Failed to process'),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')


class TestGetParamsForFormat:
    def setup_method(self):
        self.parser = UnstructuredDocumentParser(url=BASE_URL)

    def test_pdf_uses_auto_strategy(self):
        params = self.parser._get_params_for_format('report.pdf')
        assert params['strategy'] == 'auto'
        assert params.get('pdf_infer_table_structure') == 'true'

    def test_pptx_uses_fast_strategy(self):
        params = self.parser._get_params_for_format('slides.pptx')
        assert params['strategy'] == 'fast'

    def test_docx_uses_fast_strategy(self):
        params = self.parser._get_params_for_format('doc.docx')
        assert params['strategy'] == 'fast'

    def test_html_uses_auto_strategy(self):
        params = self.parser._get_params_for_format('page.html')
        assert params['strategy'] == 'auto'

    def test_unknown_extension_uses_auto(self):
        params = self.parser._get_params_for_format('file.xyz')
        assert params['strategy'] == 'auto'


class TestHealthCheck:
    def test_healthy_on_200(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('requests.get', return_value=mock_response):
            result = parser.health_check()
        assert result['status'] == 'healthy'
        assert result['url'] == BASE_URL

    def test_unhealthy_on_connection_error(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        with patch('requests.get', side_effect=requests.exceptions.ConnectionError('refused')):
            result = parser.health_check()
        assert result['status'] == 'unhealthy'
        assert 'error' in result

    def test_hits_healthcheck_endpoint(self):
        parser = UnstructuredDocumentParser(url=BASE_URL)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('requests.get', return_value=mock_response) as mock_get:
            parser.health_check()
        called_url = mock_get.call_args[0][0]
        assert called_url.endswith('/healthcheck')
