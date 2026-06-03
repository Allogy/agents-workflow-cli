"""Unit tests for services/document_parsing/docling_adapter.py (adapter class only).

Element mapper tests are in test_element_mapper.py.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from document_parsing.docling_adapter import DoclingServeDocumentParser

pytestmark = pytest.mark.unit

BASE_URL = 'http://docling-serve:5001'

_MINIMAL_DOCLING_DOCUMENT = {
    'filename': 'test.pdf',
    'md_content': '',
    'doctags_content': '<doctag><text><loc_0><loc_0><loc_0><loc_0>hello</text>\n</doctag>',
    'json_content': {
        'body': {'self_ref': '#/body', 'children': []},
        'texts': [],
        'tables': [],
        'pictures': [],
        'groups': [],
    },
}

_TASK_STATUS_PENDING = {
    'task_id': 'abc-123',
    'task_type': 'convert',
    'task_status': 'started',
    'task_meta': {'num_docs': 1, 'num_processed': 0},
}

_TASK_STATUS_SUCCESS = {
    'task_id': 'abc-123',
    'task_type': 'convert',
    'task_status': 'success',
    'task_meta': {'num_docs': 1, 'num_processed': 1, 'num_succeeded': 1},
}

_TASK_STATUS_FAILURE = {
    'task_id': 'abc-123',
    'task_type': 'convert',
    'task_status': 'failure',
    'task_meta': {'num_docs': 1, 'num_processed': 1, 'num_failed': 1},
}

_RESULT_RESPONSE = {
    'document': _MINIMAL_DOCLING_DOCUMENT,
    'status': 'success',
    'errors': [],
    'processing_time': 1.5,
}


def _mock_get_side_effect(poll_responses, result_response):
    """Build a side_effect for requests.get that handles poll and result URLs."""
    poll_iter = iter(poll_responses)

    def side_effect(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        if '/v1/status/poll/' in url:
            mock_resp.json.return_value = next(poll_iter)
        elif '/v1/result/' in url:
            mock_resp.json.return_value = result_response
        elif url.endswith('/health'):
            pass
        return mock_resp

    return side_effect


def _mock_post_submit(task_id='abc-123'):
    """Build a mock response for the async submit endpoint."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        'task_id': task_id,
        'task_type': 'convert',
        'task_status': 'pending',
        'task_meta': {'num_docs': 1},
    }
    return mock_resp


class TestDoclingServeDocumentParserParse:
    def test_raises_value_error_for_oversized_file(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        huge = b'x' * (52_428_800 + 1)
        with pytest.raises(ValueError, match='exceeds the 50 MB limit'):
            parser.parse(huge, 'big.pdf')

    def test_text_file_bypasses_http(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with patch('requests.post') as mock_post:
            result = parser.parse(b'hello world', 'readme.txt')
        mock_post.assert_not_called()
        assert len(result) == 1
        assert result[0]['type'] == 'NarrativeText'

    def test_pdf_uses_async_submit_poll_fetch(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect([_TASK_STATUS_SUCCESS], _RESULT_RESPONSE),
            ),
        ):
            result = parser.parse(b'%PDF-1.4', 'doc.pdf')
        assert isinstance(result, list)

    def test_async_submit_url_is_correct(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with (
            patch('requests.post', return_value=_mock_post_submit()) as mock_post,
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect([_TASK_STATUS_SUCCESS], _RESULT_RESPONSE),
            ),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')
        called_url = mock_post.call_args[1].get(
            'url', mock_post.call_args[0][0] if mock_post.call_args[0] else None
        )
        if called_url is None:
            called_url = mock_post.call_args[0][0]
        assert called_url == f'{BASE_URL}/v1/convert/file/async'

    def test_polls_until_terminal_status(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        poll_responses = [_TASK_STATUS_PENDING, _TASK_STATUS_PENDING, _TASK_STATUS_SUCCESS]
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect(poll_responses, _RESULT_RESPONSE),
            ) as mock_get,
        ):
            result = parser.parse(b'%PDF-1.4', 'doc.pdf')
        # 3 poll calls + 1 result fetch = 4 GET calls
        assert mock_get.call_count == 4
        assert isinstance(result, list)

    def test_handles_null_task_meta(self):
        # Regression (RAG-1666): docling returns task_meta: null while a job is
        # pending; the poll loop must not crash on task_meta.get(...).
        null_meta_pending = {'task_id': 'abc-123', 'task_status': 'started', 'task_meta': None}
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect(
                    [null_meta_pending, _TASK_STATUS_SUCCESS], _RESULT_RESPONSE
                ),
            ),
        ):
            result = parser.parse(b'%PDF-1.4', 'doc.pdf')
        assert isinstance(result, list)

    def test_unwraps_document_from_result_envelope(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect([_TASK_STATUS_SUCCESS], _RESULT_RESPONSE),
            ),
        ):
            result = parser.parse(b'%PDF-1.4', 'doc.pdf')
        assert isinstance(result, list)

    def test_default_timeouts_sized_for_large_pdfs(self):
        # Regression (RAG-1666): defaults must accommodate large VLM jobs and
        # max_poll_seconds must exceed document_timeout.
        p = DoclingServeDocumentParser(url=BASE_URL)
        assert p.document_timeout == 5400
        assert p.max_poll_seconds == 6000
        assert p.max_poll_seconds > p.document_timeout

    def test_captures_last_doctags(self):
        # parse() should stash doctags_content for sidecar preservation (RAG-1666).
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect([_TASK_STATUS_SUCCESS], _RESULT_RESPONSE),
            ),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')
        assert '<doctag>' in parser.last_doctags

    def test_last_doctags_reset_for_text_bypass(self):
        # Text-format bypass returns without a Docling call → no stale doctags.
        parser = DoclingServeDocumentParser(url=BASE_URL)
        parser.last_doctags = '<doctag>stale</doctag>'
        with patch('requests.post') as mock_post:
            parser.parse(b'hello world', 'readme.txt')
        mock_post.assert_not_called()
        assert parser.last_doctags == ''
        parser = DoclingServeDocumentParser(url=BASE_URL, max_retries=3)
        mock_response = MagicMock()
        mock_response.status_code = 422
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        with (
            patch('requests.post', return_value=mock_response),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')

    def test_connection_error_retried_then_raises_runtime_error(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, max_retries=2)
        with (
            patch(
                'requests.post',
                side_effect=requests.exceptions.ConnectionError('refused'),
            ),
            patch.object(parser, '_wait_for_healthy'),
            patch('time.sleep'),
            pytest.raises(RuntimeError, match='Failed to process'),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')

    def test_terminal_failure_not_retried(self):
        # RAG-1666 hardening: a terminal Docling 'failure' (e.g. un-renderable
        # page) must raise DoclingConversionError immediately WITHOUT retrying —
        # retrying re-renders the whole doc and re-incurs per-page model cost.
        from document_parsing.docling_adapter import DoclingConversionError

        parser = DoclingServeDocumentParser(url=BASE_URL, max_retries=3)
        post_calls = {'n': 0}

        def post_side_effect(*args, **kwargs):
            post_calls['n'] += 1
            return _mock_post_submit('task-1')

        def get_side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            if '/v1/status/poll/' in url:
                mock_resp.json.return_value = _TASK_STATUS_FAILURE
            elif url.endswith('/health'):
                mock_resp.json.return_value = {'status': 'ok'}
            return mock_resp

        with (
            patch('requests.post', side_effect=post_side_effect),
            patch('requests.get', side_effect=get_side_effect),
            patch.object(parser, '_wait_for_healthy'),
            patch('time.sleep'),
            pytest.raises(DoclingConversionError, match='non-retryable'),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')
        # Submitted exactly once — no whole-document retry.
        assert post_calls['n'] == 1

    def test_poll_timeout_raises_runtime_error(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, max_retries=1, max_poll_seconds=0)
        with (
            patch('requests.post', return_value=_mock_post_submit()),
            patch(
                'requests.get',
                side_effect=_mock_get_side_effect([_TASK_STATUS_PENDING], _RESULT_RESPONSE),
            ),
            pytest.raises(RuntimeError, match='Polling timed out'),
        ):
            parser.parse(b'%PDF-1.4', 'doc.pdf')


class TestPageBatching:
    """Page-range batching for large PDFs (RAG-1666 page-split).

    These tests isolate the batching/merge logic by mocking the
    per-batch HTTP call and the element mapper (which has its own tests).
    """

    @staticmethod
    def _resp(text):
        return {'doctags_content': f'<doctag><text>{text}</text></doctag>'}

    def test_disabled_by_default_single_job(self):
        # page_batch_size=0 → no page_range, single whole-document job.
        parser = DoclingServeDocumentParser(url=BASE_URL)  # default 0
        assert parser.page_batch_size == 0

    def test_batches_large_pdf_and_merges(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, page_batch_size=2)
        captured_ranges = []

        def fake_call(file_content, filename, params):
            captured_ranges.append(params.get('page_range'))
            return self._resp(str(params.get('page_range')))

        with (
            patch.object(parser, '_call_with_retry', side_effect=fake_call),
            patch(
                'document_parsing.docling_adapter.map_docling_to_elements',
                return_value=[{'type': 'NarrativeText', 'text': 'x'}],
            ),
            patch(
                'document_parsing.docling_adapter._count_pdf_pages',
                return_value=5,
            ),
        ):
            elements = parser.parse(b'%PDF-1.4', 'big.pdf')
        # 5 pages, batch 2 → ranges [1,2],[3,4],[5,5]
        assert captured_ranges == [[1, 2], [3, 4], [5, 5]]
        # One element per batch → 3 merged.
        assert len(elements) == 3
        # DocTags concatenated from all 3 batches.
        assert parser.last_doctags.count('<doctag>') == 3

    def test_bad_batch_skipped_others_kept(self):
        from document_parsing.docling_adapter import DoclingConversionError

        parser = DoclingServeDocumentParser(url=BASE_URL, page_batch_size=2)

        def fake_call(file_content, filename, params):
            if params.get('page_range') == [3, 4]:
                raise DoclingConversionError('bad page in batch')
            return self._resp('ok')

        with (
            patch.object(parser, '_call_with_retry', side_effect=fake_call),
            patch(
                'document_parsing.docling_adapter.map_docling_to_elements',
                return_value=[{'type': 'NarrativeText', 'text': 'x'}],
            ),
            patch(
                'document_parsing.docling_adapter._count_pdf_pages',
                return_value=5,
            ),
        ):
            elements = parser.parse(b'%PDF-1.4', 'big.pdf')
        # Batches [1,2] and [5,5] succeed; [3,4] skipped → 2 elements.
        assert len(elements) == 2

    def test_all_batches_fail_raises(self):
        from document_parsing.docling_adapter import DoclingConversionError

        parser = DoclingServeDocumentParser(url=BASE_URL, page_batch_size=2)
        with (
            patch.object(
                parser,
                '_call_with_retry',
                side_effect=DoclingConversionError('bad'),
            ),
            patch(
                'document_parsing.docling_adapter._count_pdf_pages',
                return_value=4,
            ),
            pytest.raises(DoclingConversionError, match='All page batches failed'),
        ):
            parser.parse(b'%PDF-1.4', 'big.pdf')

    def test_small_pdf_single_job_even_when_enabled(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, page_batch_size=50)
        calls = []

        def fake_call(file_content, filename, params):
            calls.append(params.get('page_range'))
            return self._resp('small')

        with (
            patch.object(parser, '_call_with_retry', side_effect=fake_call),
            patch(
                'document_parsing.docling_adapter.map_docling_to_elements',
                return_value=[{'type': 'NarrativeText', 'text': 'x'}],
            ),
            patch(
                'document_parsing.docling_adapter._count_pdf_pages',
                return_value=10,
            ),
        ):
            parser.parse(b'%PDF-1.4', 'small.pdf')
        # 10 pages <= batch 50 → single job, no page_range.
        assert calls == [None]


class TestGetParamsForFormat:
    def setup_method(self):
        self.parser = DoclingServeDocumentParser(url=BASE_URL)

    def test_pdf_includes_ocr(self):
        params = self.parser._get_params_for_format('report.pdf')
        assert params.get('do_ocr') == 'true'
        assert params.get('pdf_backend') == 'dlparse_v2'

    def test_non_pdf_no_ocr(self):
        params = self.parser._get_params_for_format('doc.docx')
        assert 'do_ocr' not in params

    def test_all_formats_include_json_and_md_output(self):
        for filename in ('doc.docx', 'slides.pptx', 'report.pdf', 'page.html'):
            params = self.parser._get_params_for_format(filename)
            assert 'json' in params['to_formats']
            assert 'md' in params['to_formats']

    def test_table_structure_enabled(self):
        params = self.parser._get_params_for_format('doc.docx')
        assert params.get('do_table_structure') == 'true'

    def test_document_timeout_included(self):
        params = self.parser._get_params_for_format('doc.pdf')
        # Default raised to 5400s (90 min) to fit large multi-hundred-page PDFs.
        assert params.get('document_timeout') == '5400'

    def test_custom_document_timeout(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, document_timeout=300)
        params = parser._get_params_for_format('doc.pdf')
        assert params.get('document_timeout') == '300'

    # --- VLM pipeline selection (RAG-1666) ---

    def test_standard_pipeline_by_default(self):
        params = self.parser._get_params_for_format('doc.pdf')
        assert params['pipeline'] == 'standard'
        assert 'vlm_pipeline_preset' not in params

    def test_vlm_pipeline_when_preset_set(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, vlm_pipeline_preset='bedrock-proxy')
        params = parser._get_params_for_format('doc.pdf')
        assert params['pipeline'] == 'vlm'
        assert params['vlm_pipeline_preset'] == 'bedrock-proxy'
        # OCR / table-mode / PDF-backend do not apply to the VLM pipeline.
        assert 'do_ocr' not in params
        assert 'pdf_backend' not in params
        assert 'table_mode' not in params

    def test_vlm_pipeline_independent_of_extension(self):
        parser = DoclingServeDocumentParser(url=BASE_URL, vlm_pipeline_preset='bedrock-proxy')
        pdf_params = parser._get_params_for_format('doc.pdf')
        docx_params = parser._get_params_for_format('doc.docx')
        assert pdf_params['pipeline'] == 'vlm'
        assert pdf_params == docx_params

    def test_doctags_requested_in_both_pipelines(self):
        # DocTags must be requested so it can be preserved as a sidecar (RAG-1666).
        std = DoclingServeDocumentParser(url=BASE_URL)._get_params_for_format('doc.pdf')
        vlm = DoclingServeDocumentParser(
            url=BASE_URL, vlm_pipeline_preset='bedrock-proxy'
        )._get_params_for_format('doc.pdf')
        assert 'doctags' in std['to_formats']
        assert 'doctags' in vlm['to_formats']


class TestHealthCheck:
    def test_healthy_on_200(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('requests.get', return_value=mock_response):
            result = parser.health_check()
        assert result['status'] == 'healthy'
        assert result['url'] == BASE_URL

    def test_unhealthy_on_connection_error(self):
        parser = DoclingServeDocumentParser(url=BASE_URL)
        with patch(
            'requests.get',
            side_effect=requests.exceptions.ConnectionError('refused'),
        ):
            result = parser.health_check()
        assert result['status'] == 'unhealthy'
        assert 'error' in result

    def test_hits_health_not_healthcheck(self):
        """Docling Serve uses /health, not /healthcheck."""
        parser = DoclingServeDocumentParser(url=BASE_URL)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        with patch('requests.get', return_value=mock_response) as mock_get:
            parser.health_check()
        called_url = mock_get.call_args[0][0]
        assert called_url.endswith('/health')
        assert 'healthcheck' not in called_url
