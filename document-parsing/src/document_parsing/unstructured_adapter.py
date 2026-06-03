"""Unstructured API adapter for document parsing.

Implements ``DocumentParserPort`` by calling the self-hosted Unstructured
API. Unifies two previously separate calling paths:

- Real-time path (``services.data_ingest.process_documents.DocumentProcessor``):
  no retries, ``auto`` strategy for PDFs.
- Batch KB pipeline path
  (``kb-ingest-pipeline/runners/contextual_chunking_handler.py``):
  3 retries with 30 s back-off, ``hi_res`` strategy for PDFs.

Both paths are now handled by a single class. Retry behavior is controlled
by ``max_retries`` (default 3). The PDF strategy is ``auto`` to avoid
triggering the 2 GB YOLOX model load that ``hi_res`` requires; callers
that need ``hi_res`` can subclass and override ``_get_params_for_format``.
"""

import io
import logging
import pathlib
import time
from typing import Any

import requests

from document_parsing.utils import check_file_size, try_direct_parse

logger = logging.getLogger(__name__)


class UnstructuredDocumentParser:
    """Document parser adapter that calls the Unstructured API.

    Unifies the real-time (DocumentProcessor) and batch (KB pipeline)
    parsing paths behind a single interface. Retry behavior is
    controlled by ``max_retries``.

    Args:
        url: Base URL of the Unstructured API, e.g.
            ``http://unstructured:8125``.
        max_retries: Number of POST attempts before raising. Defaults to
            3 (matching the batch KB pipeline). Pass ``1`` to disable
            retries (real-time path behaviour).
    """

    def __init__(self, url: str, max_retries: int = 3):
        self.url = url
        self.max_retries = max_retries

    def parse(self, file_content: bytes, filename: str) -> list[dict[str, Any]]:
        """Parse a document and return Unstructured-style element dicts.

        Args:
            file_content: Raw document bytes.
            filename: Original filename; used for format detection and
                error messages.

        Returns:
            A list of element dicts as returned by the Unstructured API,
            or a single-element list for text-format bypasses.

        Raises:
            ValueError: When ``file_content`` exceeds the 50 MB size
                limit (delegated to ``check_file_size``).
            RuntimeError: When all retry attempts are exhausted.
            requests.exceptions.HTTPError: For 4xx client errors (not
                retried).
        """
        # 1. File-size guard (50 MB limit)
        check_file_size(file_content, filename)
        # 2. Text-format bypass (.txt, .md, .csv)
        direct = try_direct_parse(file_content, filename)
        if direct is not None:
            return direct
        # 3. Per-format strategy selection
        params = self._get_params_for_format(filename)
        # 4. HTTP POST with retry
        return self._call_with_retry(file_content, filename, params)

    def health_check(self) -> dict[str, Any]:
        """Probe the Unstructured service health endpoint.

        Returns:
            A dict with ``status`` (``'healthy'`` or ``'unhealthy'``),
            ``url``, and on failure an ``error`` key with the exception
            message.
        """
        try:
            response = requests.get(f'{self.url}/healthcheck', timeout=10)
            response.raise_for_status()
            return {'status': 'healthy', 'url': self.url}
        except requests.exceptions.RequestException as e:
            return {'status': 'unhealthy', 'url': self.url, 'error': str(e)}

    def _get_params_for_format(self, filename: str) -> dict[str, Any]:
        """Select Unstructured strategy and parameters per file type.

        Merges the logic from ``DocumentProcessor._get_optimal_parameters``
        and the KB pipeline's ``process_document_with_unstructured`` into
        one method. The KB pipeline used ``hi_res`` for PDFs while the
        real-time path used ``auto``. This adapter uses ``auto`` because
        ``hi_res`` triggers a 2 GB YOLOX model load and is much slower.
        Callers that need ``hi_res`` can subclass and override this method.

        Args:
            filename: Original filename; only the suffix is examined.

        Returns:
            A dict of Unstructured POST parameters (strategy, language,
            etc.) suitable for passing as ``data`` to
            ``requests.post(...)``.
        """
        ext = pathlib.Path(filename).suffix.lower()
        params: dict[str, Any] = {'languages': 'eng'}

        if ext == '.pdf':
            params.update(
                {
                    'strategy': 'auto',
                    'pdf_infer_table_structure': 'true',
                    'extract_image_block_types': ['Image', 'Table'],
                    'split_pdf_page': 'true',
                    'split_pdf_allow_failed': 'true',
                    'split_pdf_concurrency_level': '10',
                }
            )
        elif ext == '.pptx':
            params.update(
                {
                    'strategy': 'fast',
                    'extract_image_block_types': ['Image', 'Table'],
                    'infer_table_structure': 'true',
                }
            )
        elif ext == '.docx':
            params.update(
                {
                    'strategy': 'fast',
                    'infer_table_structure': 'true',
                }
            )
        elif ext in ('.html', '.htm'):
            params['strategy'] = 'auto'
        elif ext in ('.txt', '.md', '.csv'):
            params['strategy'] = 'fast'
        else:
            params['strategy'] = 'auto'

        return params

    def _call_with_retry(
        self,
        file_content: bytes,
        filename: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """POST to the Unstructured API with retry and health-check gate.

        Attempts up to ``self.max_retries`` times. Between attempts a
        30 s × attempt back-off is applied, followed by a health-check
        gate (up to 180 s) before the next attempt. Connection errors,
        read timeouts, and 5xx HTTP errors are retried; 4xx errors are
        raised immediately.

        Args:
            file_content: Raw document bytes to upload.
            filename: Original filename, forwarded in the multipart form.
            params: Unstructured POST parameters from
                ``_get_params_for_format``.

        Returns:
            Parsed element dicts from the Unstructured API JSON response.

        Raises:
            RuntimeError: When all attempts are exhausted.
            requests.exceptions.HTTPError: For 4xx client errors.
        """
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            file_stream = io.BytesIO(file_content)
            files = {'files': (filename, file_stream)}

            try:
                logger.info(
                    f'Sending {filename} to Unstructured '
                    f'(attempt {attempt}/{self.max_retries}, '
                    f'strategy={params.get("strategy", "auto")})'
                )
                request_start = time.time()

                resp = requests.post(
                    f'{self.url}/general/v0/general',
                    files=files,
                    data=params,
                    timeout=900,
                )
                duration = time.time() - request_start

                resp.raise_for_status()

                elements = resp.json()
                logger.info(
                    f'Parsed {filename}: {len(elements)} elements '
                    f'(strategy={params.get("strategy", "auto")}, '
                    f'duration={duration:.1f}s)'
                )
                return elements

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                logger.warning(
                    f'Connection error on attempt {attempt}/{self.max_retries} for {filename}: {e}'
                )
            except requests.exceptions.ReadTimeout as e:
                last_exception = e
                logger.warning(
                    f'Read timeout on attempt {attempt}/{self.max_retries} for {filename}: {e}'
                )
            except requests.exceptions.HTTPError as e:
                # Don't retry client errors (4xx)
                if e.response is not None and e.response.status_code < 500:
                    raise
                last_exception = e
                logger.warning(
                    f'HTTP error on attempt {attempt}/{self.max_retries} for {filename}: {e}'
                )

            # Backoff + health gate before retry
            if attempt < self.max_retries:
                backoff = 30 * attempt
                logger.info(f'Waiting {backoff}s before retry...')
                time.sleep(backoff)
                self._wait_for_healthy(max_wait=180)

        raise RuntimeError(
            f'Failed to process {filename} after {self.max_retries} attempts. '
            f'Last error: {last_exception}'
        )

    def _wait_for_healthy(self, max_wait: int = 120) -> None:
        """Wait for the Unstructured service to become healthy.

        Polls ``/healthcheck`` every 5 seconds up to *max_wait* seconds.
        Logs a warning and returns without raising if the service does
        not recover in time (the subsequent POST attempt will fail and
        be retried or re-raise).

        Args:
            max_wait: Maximum number of seconds to wait before giving up.
        """
        start = time.time()
        while time.time() - start < max_wait:
            result = self.health_check()
            if result.get('status') == 'healthy':
                elapsed = time.time() - start
                logger.info(f'Unstructured service is healthy (waited {elapsed:.1f}s)')
                return
            time.sleep(5)
        logger.warning(f'Unstructured service did not recover within {max_wait}s')
