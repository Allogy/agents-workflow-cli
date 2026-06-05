"""Docling Serve adapter for document parsing.

Implements ``DocumentParserPort`` by calling the Docling Serve async
API. The three-step flow avoids HTTP timeouts on large documents:

1. Submit: ``POST /v1/convert/file/async`` → ``task_id``
2. Poll: ``GET /v1/status/poll/{task_id}?wait=N`` (long-polling)
3. Fetch: ``GET /v1/result/{task_id}`` → document response

Docling Serve provides high-quality document conversion with native
support for tables, images, and hierarchical document structure.

The adapter maps Docling's ``DoclingDocument`` JSON schema to the
Unstructured-compatible element dicts that the rest of the platform
expects, enabling transparent backend switching without downstream
changes.
"""

import io
import logging
import pathlib
import time
from typing import Any

import requests

from document_parsing.utils import check_file_size, try_direct_parse

logger = logging.getLogger(__name__)


class DoclingConversionError(RuntimeError):
    """Terminal, non-retryable Docling conversion failure.

    Raised when Docling reports a terminal ``failure``/``skipped`` task
    status (e.g. a page that cannot be rendered — ``cannot write empty
    image``). These are deterministic: retrying the whole document would
    hit the same error and re-incur per-page model cost, so the retry
    loop must NOT catch this. Distinct from transient errors (connection,
    read timeout, 5xx) which remain retryable.
    """


def _count_pdf_pages(file_content: bytes) -> int | None:
    """Return the page count of a PDF, or None if it can't be determined.

    Used to drive page-range batching. Failures (encrypted/corrupt PDF,
    missing dependency) return None so the caller falls back to a single
    whole-document conversion.
    """
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(file_content))
        return len(reader.pages)
    except Exception as e:
        logger.warning(f'Could not count PDF pages for batching: {e}')
        return None


# ──────────────────────────────────────────────────────────────────────
# Label → element type mapping
# ──────────────────────────────────────────────────────────────────────

# Maps Docling ``label`` values (from texts[], tables[], pictures[])
# to Unstructured element type strings. The downstream chunker relies
# on ``Title`` elements for section boundary detection.
_TERMINAL_STATUSES = frozenset({'success', 'partial_success', 'failure', 'skipped'})

_LABEL_TO_TYPE: dict[str, str] = {
    # texts[] labels
    'section_header': 'Title',
    'title': 'Title',
    'subtitle': 'Title',
    'text': 'NarrativeText',
    'paragraph': 'NarrativeText',
    'caption': 'NarrativeText',
    'footnote': 'NarrativeText',
    'formula': 'NarrativeText',
    'code': 'NarrativeText',
    'list_item': 'ListItem',
    'page_header': 'Header',
    'page_footer': 'Header',
    # tables[] labels
    'table': 'Table',
    'document_index': 'Table',
    # pictures[] labels
    'picture': 'Image',
    'chart': 'Image',
}


class DoclingServeDocumentParser:
    """Document parser adapter that calls the Docling Serve async API.

    Provides the same interface as ``UnstructuredDocumentParser`` but
    targets Docling Serve's async conversion endpoints:

    1. ``POST /v1/convert/file/async`` → ``task_id``
    2. ``GET /v1/status/poll/{task_id}?wait=N`` (long-polling)
    3. ``GET /v1/result/{task_id}`` → document response

    This avoids HTTP timeouts on large documents that the synchronous
    ``/v1/convert/file`` endpoint is prone to. Retry behavior mirrors
    the Unstructured adapter (configurable attempts with 30 s back-off
    and a health-check gate between retries).

    Args:
        url: Base URL of the Docling Serve API, e.g.
            ``http://docling-serve:5001``.
        max_retries: Number of submit attempts before raising.
            Defaults to 3.
        poll_wait: Seconds for the long-poll ``wait`` query parameter.
            The server holds the connection up to this many seconds
            before returning a still-pending status. Defaults to 30.
        document_timeout: Server-side per-document processing timeout
            in seconds, passed as the ``document_timeout`` form field.
            Defaults to 5400 (90 min) to accommodate large multi-hundred-page
            PDFs through the VLM pipeline (per-page model calls). Docling
            otherwise drops un-processed pages when this elapses.
        max_poll_seconds: Maximum total wall-clock seconds to poll
            before giving up. Covers queue wait + processing time. Must
            exceed ``document_timeout``. Defaults to 6000 (100 minutes).
    """

    def __init__(
        self,
        url: str,
        max_retries: int = 3,
        poll_wait: int = 30,
        document_timeout: int = 5400,
        max_poll_seconds: int = 6000,
        vlm_pipeline_preset: str | None = None,
        page_batch_size: int = 0,
    ):
        self.url = url
        self.max_retries = max_retries
        self.poll_wait = poll_wait
        self.document_timeout = document_timeout
        self.max_poll_seconds = max_poll_seconds
        # When set, use Docling's VLM pipeline with this preset (e.g.
        # "bedrock-proxy" → VLM Proxy → Bedrock) instead of the local CPU
        # "standard" pipeline. The preset must exist on the Docling Serve
        # instance (DOCLING_SERVE_CUSTOM_VLM_PRESETS).
        self.vlm_pipeline_preset = vlm_pipeline_preset
        # Holds the ``doctags_content`` from the most recent successful parse,
        # or '' if none. Callers (e.g. the pgvector runner) may read this after
        # ``parse()`` to preserve Docling's native DocTags format as a sidecar
        # artifact. Reset at the start of each ``parse()`` call.
        self.last_doctags: str = ''
        # Markdown rendering and DoclingDocument JSON from the most recent
        # successful parse. ``last_markdown`` is '' and ``last_json`` is None
        # when the parse produced none (e.g. the text-format bypass). For
        # page-batched PDFs, markdown parts are joined with blank lines and
        # ``last_json`` is a LIST of per-batch DoclingDocument objects
        # (DoclingDocument JSON cannot be concatenated). Reset at the start
        # of each ``parse()`` call.
        self.last_markdown: str = ''
        self.last_json: dict[str, Any] | list[dict[str, Any]] | None = None
        # When > 0, PDFs are converted in page-range batches of this size
        # instead of one whole-document job. Each batch is an independent
        # Docling job, so a single un-renderable page (e.g. "cannot write
        # empty image") only voids its own batch — the rest of the document
        # still produces chunks. Uses Docling's native ``page_range`` option.
        # 0 disables batching (single whole-document job).
        self.page_batch_size = page_batch_size

    def parse(self, file_content: bytes, filename: str) -> list[dict[str, Any]]:
        """Parse a document and return Unstructured-compatible element dicts.

        Args:
            file_content: Raw document bytes.
            filename: Original filename; used for format detection and
                error messages.

        Returns:
            A list of element dicts compatible with the Unstructured
            element schema, or a single-element list for text-format
            bypasses.

        Raises:
            ValueError: When ``file_content`` exceeds the 50 MB size
                limit.
            RuntimeError: When all retry attempts are exhausted.
            requests.exceptions.HTTPError: For 4xx client errors (not
                retried).
        """
        # Reset per-call artifact capture.
        self.last_doctags = ''
        self.last_markdown = ''
        self.last_json = None
        # 1. File-size guard (50 MB limit)
        check_file_size(file_content, filename)
        # 2. Text-format bypass (.txt, .md, .csv)
        direct = try_direct_parse(file_content, filename)
        if direct is not None:
            return direct
        # 3. Per-format parameter selection
        params = self._get_params_for_format(filename)
        # 4. Page-batched conversion for large PDFs (resilient to a single
        #    un-renderable page) when enabled; otherwise a single job.
        ext = pathlib.Path(filename).suffix.lower()
        if self.page_batch_size > 0 and ext == '.pdf':
            return self._parse_in_page_batches(file_content, filename, params)
        # 5. Single whole-document conversion.
        response = self._call_with_retry(file_content, filename, params)
        # Preserve Docling's native outputs (sidecars), then map to elements.
        self.last_doctags = response.get('doctags_content') or ''
        self.last_markdown = response.get('md_content') or ''
        self.last_json = response.get('json_content') or None
        return map_docling_to_elements(response, filename)

    def _parse_in_page_batches(
        self,
        file_content: bytes,
        filename: str,
        base_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Convert a PDF in independent ``page_range`` batches.

        Splits the document into ranges of ``page_batch_size`` pages and
        converts each as a separate Docling job (using the native
        ``page_range`` option — no physical PDF splitting). A batch that
        fails terminally (e.g. an un-renderable page) is logged and
        skipped, so one bad page only loses its batch rather than the whole
        document. Element lists are concatenated in page order; DocTags are joined
        into ``self.last_doctags``, markdown parts into ``self.last_markdown``, and
        per-batch DoclingDocument JSON objects are collected as a list in
        ``self.last_json``.

        Falls back to a single whole-document job when the page count
        cannot be determined.
        """
        num_pages = _count_pdf_pages(file_content)
        if num_pages is None or num_pages <= self.page_batch_size:
            # Small or uncountable — one job is fine.
            response = self._call_with_retry(file_content, filename, base_params)
            self.last_doctags = response.get('doctags_content') or ''
            self.last_markdown = response.get('md_content') or ''
            self.last_json = response.get('json_content') or None
            return map_docling_to_elements(response, filename)

        logger.info(
            f'Page-batched conversion for {filename}: {num_pages} pages '
            f'in batches of {self.page_batch_size}'
        )
        all_elements: list[dict[str, Any]] = []
        doctags_parts: list[str] = []
        md_parts: list[str] = []
        json_parts: list[dict[str, Any]] = []
        failed_batches: list[str] = []

        for start in range(1, num_pages + 1, self.page_batch_size):
            end = min(start + self.page_batch_size - 1, num_pages)
            batch_params = dict(base_params)
            # Docling page_range is 1-indexed, inclusive: [start, end].
            batch_params['page_range'] = [start, end]
            batch_label = f'pages {start}-{end}'
            try:
                response = self._call_with_retry(
                    file_content, f'{filename} ({batch_label})', batch_params
                )
                elements = map_docling_to_elements(response, filename)
                all_elements.extend(elements)
                if response.get('doctags_content'):
                    doctags_parts.append(response['doctags_content'])
                if response.get('md_content'):
                    md_parts.append(response['md_content'])
                if response.get('json_content'):
                    json_parts.append(response['json_content'])
                logger.info(f'Batch {batch_label} of {filename}: {len(elements)} elements')
            except DoclingConversionError as e:
                # Bad page in this batch — skip it, keep the rest.
                failed_batches.append(batch_label)
                logger.error(
                    f'Skipping batch {batch_label} of {filename} (terminal conversion failure): {e}'
                )
            except Exception as e:  # noqa: BLE001
                # Any other per-batch error (e.g. Docling returned a success
                # envelope with document=None after a page pipeline error, or a
                # malformed result that fails element mapping). Skip this batch
                # so one bad batch does not lose the whole document — the
                # page-batched path is meant to be resilient (RAG-1660).
                failed_batches.append(batch_label)
                logger.error(
                    f'Skipping batch {batch_label} of {filename} '
                    f'(unexpected error during conversion/mapping): {e!r}'
                )

        self.last_doctags = '\n'.join(doctags_parts)
        self.last_markdown = '\n\n'.join(md_parts)
        self.last_json = json_parts or None
        if failed_batches:
            logger.warning(
                f'{filename}: {len(failed_batches)} batch(es) skipped due to '
                f'conversion failures: {", ".join(failed_batches)}. '
                f'Produced {len(all_elements)} elements from the rest.'
            )
        if not all_elements and failed_batches:
            raise DoclingConversionError(
                f'All page batches failed for {filename} ({len(failed_batches)} batches)'
            )
        return all_elements

    def health_check(self) -> dict[str, Any]:
        """Probe the Docling Serve health endpoint.

        Docling Serve exposes ``/health`` (not ``/healthcheck``).

        Returns:
            A dict with ``status`` (``'healthy'`` or ``'unhealthy'``),
            ``url``, and on failure an ``error`` key with the exception
            message.
        """
        try:
            response = requests.get(f'{self.url}/health', timeout=10)
            response.raise_for_status()
            return {'status': 'healthy', 'url': self.url}
        except requests.exceptions.RequestException as e:
            return {'status': 'unhealthy', 'url': self.url, 'error': str(e)}

    def _get_params_for_format(self, filename: str) -> dict[str, Any]:
        """Build Docling Serve form-data parameters per file type.

        Two modes:

        - **Standard (CPU) pipeline** (default, when ``vlm_pipeline_preset``
          is unset): local OCR + layout analysis. All files request JSON and
          Markdown output, table structure extraction, and embedded image
          export. PDFs additionally enable OCR and the ``dlparse_v2`` backend.
        - **VLM pipeline** (when ``vlm_pipeline_preset`` is set): each page is
          rendered as an image and sent to the configured VLM preset (e.g.
          ``bedrock-proxy`` → VLM Proxy → Bedrock Claude). OCR / table-mode /
          PDF-backend options do not apply — the vision model performs the
          conversion — so only the VLM-relevant fields are sent.

        Args:
            filename: Original filename; only the suffix is examined.

        Returns:
            A dict of form-data parameters for Docling Serve. List
            values (e.g. ``to_formats``) must be expanded to repeated
            form fields by the caller.
        """
        ext = pathlib.Path(filename).suffix.lower()

        # VLM pipeline: delegate parsing to the vision model via the preset.
        if self.vlm_pipeline_preset:
            return {
                'to_formats': ['json', 'md', 'doctags'],
                'do_table_structure': 'true',
                'include_images': 'true',
                'pipeline': 'vlm',
                'vlm_pipeline_preset': self.vlm_pipeline_preset,
                'image_export_mode': 'embedded',
                'document_timeout': str(self.document_timeout),
                # Don't abort the whole document on a per-page error — keep
                # partial results so one bad page doesn't void the doc.
                'abort_on_error': 'false',
            }

        # Standard (CPU) pipeline: local OCR + layout analysis.
        params: dict[str, Any] = {
            'to_formats': ['json', 'md', 'doctags'],
            'do_table_structure': 'true',
            'include_images': 'true',
            'table_mode': 'accurate',
            'pipeline': 'standard',
            'image_export_mode': 'embedded',
            'document_timeout': str(self.document_timeout),
            'abort_on_error': 'false',
        }
        if ext == '.pdf':
            params['do_ocr'] = 'true'
            params['pdf_backend'] = 'dlparse_v2'
        return params

    def _call_with_retry(
        self,
        file_content: bytes,
        filename: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit to Docling Serve async API with retry.

        Uses the three-step async flow to avoid HTTP timeouts on large
        documents:

        1. ``POST /v1/convert/file/async`` → ``task_id``
        2. Long-poll ``GET /v1/status/poll/{task_id}?wait=N``
        3. ``GET /v1/result/{task_id}`` → document response

        Retries the entire flow on TRANSIENT errors only — connection
        errors, read timeouts, and 5xx HTTP errors. Terminal Docling
        conversion failures (``DoclingConversionError``, e.g. an
        un-renderable page) and 4xx client errors are raised immediately
        WITHOUT retry, since re-running the whole document would deterministically
        hit the same error and re-incur per-page model cost.

        Args:
            file_content: Raw document bytes to upload.
            filename: Original filename, forwarded in the multipart
                form.
            params: Docling Serve form-data parameters from
                ``_get_params_for_format``.

        Returns:
            The document dict from the result endpoint, with
            ``json_content`` and ``md_content`` at the top level.

        Raises:
            RuntimeError: When all attempts are exhausted or polling
                times out.
            requests.exceptions.HTTPError: For 4xx client errors.
        """
        last_exception: Exception | None = None

        # Build form fields as a list of tuples to support repeated
        # keys (e.g. multiple ``to_formats`` entries).
        form_fields: list[tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, list):
                for item in value:
                    form_fields.append((key, str(item)))
            else:
                form_fields.append((key, str(value)))

        for attempt in range(1, self.max_retries + 1):
            try:
                task_id = self._submit_async(file_content, filename, form_fields, attempt)
                self._poll_until_complete(task_id, filename)
                return self._fetch_result(task_id, filename)

            except DoclingConversionError:
                # Deterministic, terminal conversion failure (e.g. an
                # un-renderable page). Retrying re-renders the whole document
                # and re-incurs per-page model cost for the same outcome —
                # fail fast instead.
                logger.error(
                    f'Non-retryable Docling conversion failure for {filename} '
                    f'(attempt {attempt}/{self.max_retries}); not retrying.'
                )
                raise
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
                if e.response is not None and e.response.status_code < 500:
                    raise
                last_exception = e
                logger.warning(
                    f'HTTP error on attempt {attempt}/{self.max_retries} for {filename}: {e}'
                )
            except RuntimeError as e:
                # Transient/unknown RuntimeError (e.g. polling timeout) — retryable.
                last_exception = e
                logger.warning(
                    f'Conversion error on attempt {attempt}/{self.max_retries} for {filename}: {e}'
                )

            if attempt < self.max_retries:
                backoff = 30 * attempt
                logger.info(f'Waiting {backoff}s before retry...')
                time.sleep(backoff)
                self._wait_for_healthy(max_wait=180)

        raise RuntimeError(
            f'Failed to process {filename} after {self.max_retries} attempts. '
            f'Last error: {last_exception}'
        )

    def _submit_async(
        self,
        file_content: bytes,
        filename: str,
        form_fields: list[tuple[str, str]],
        attempt: int,
    ) -> str:
        """Submit a file for async conversion.

        Posts to ``/v1/convert/file/async`` and returns the
        ``task_id`` from the ``TaskStatusResponse``.
        """
        file_stream = io.BytesIO(file_content)
        files = {'files': (filename, file_stream)}

        logger.info(
            f'Submitting {filename} to Docling Serve async (attempt {attempt}/{self.max_retries})'
        )

        resp = requests.post(
            f'{self.url}/v1/convert/file/async',
            files=files,
            data=form_fields,
            timeout=60,
        )
        resp.raise_for_status()

        status_response = resp.json()
        task_id = status_response['task_id']
        logger.info(f'Submitted {filename}: task_id={task_id}')
        return task_id

    def _poll_until_complete(
        self,
        task_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """Long-poll until the task reaches a terminal status.

        Uses the ``wait`` query parameter for server-side long-polling,
        avoiding rapid client-side polling. Raises ``RuntimeError`` if
        the task fails or polling exceeds ``max_poll_seconds``.

        Returns:
            The final ``TaskStatusResponse`` dict.
        """
        start = time.time()

        while True:
            elapsed = time.time() - start
            if elapsed > self.max_poll_seconds:
                raise RuntimeError(
                    f'Polling timed out for {filename} (task_id={task_id}) after {elapsed:.0f}s'
                )

            resp = requests.get(
                f'{self.url}/v1/status/poll/{task_id}',
                params={'wait': self.poll_wait},
                timeout=self.poll_wait + 10,
            )
            resp.raise_for_status()

            status_response = resp.json()
            task_status = status_response.get('task_status', '')
            # task_meta may be present-but-null in the API response, so a
            # plain .get(..., {}) default is not enough — coerce None to {}.
            task_meta = status_response.get('task_meta') or {}

            logger.info(
                f'Poll {filename} (task_id={task_id}): '
                f'status={task_status}, '
                f'processed={task_meta.get("num_processed", "?")}'
                f'/{task_meta.get("num_docs", "?")}, '
                f'elapsed={elapsed:.0f}s'
            )

            if task_status in _TERMINAL_STATUSES:
                if task_status in ('failure', 'skipped'):
                    raise DoclingConversionError(
                        f'Docling conversion failed for {filename}: '
                        f'task_status={task_status} (non-retryable — retrying the '
                        f'whole document would re-incur model cost on the same error)'
                    )
                return status_response

    def _fetch_result(
        self,
        task_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """Fetch the conversion result for a completed task.

        The result endpoint returns an ``ExportDocumentResponse`` with
        a ``document`` wrapper. This method unwraps it so the caller
        receives the document dict directly (with ``json_content`` and
        ``md_content`` at the top level), matching the shape that
        ``map_docling_to_elements`` expects.
        """
        resp = requests.get(
            f'{self.url}/v1/result/{task_id}',
            timeout=60,
        )
        resp.raise_for_status()

        result = resp.json()
        processing_time = result.get('processing_time', 0)
        errors = result.get('errors', [])

        if errors:
            error_msgs = '; '.join(
                f'{e.get("component_type", "unknown")}: {e.get("error_message", "?")}'
                for e in errors
            )
            logger.warning(f'Docling reported errors for {filename}: {error_msgs}')

        logger.info(
            f'Fetched result for {filename} (task_id={task_id}, '
            f'processing_time={processing_time:.1f}s)'
        )

        # Unwrap the document from the ExportDocumentResponse envelope
        if 'document' in result and 'json_content' not in result:
            return result['document']
        return result

    def _wait_for_healthy(self, max_wait: int = 120) -> None:
        """Wait for the Docling Serve service to become healthy.

        Polls ``/health`` every 5 seconds up to *max_wait* seconds.
        Logs a warning and returns without raising if the service does
        not recover in time (the subsequent POST attempt will fail and
        be retried or re-raise).

        Args:
            max_wait: Maximum number of seconds to wait before giving
                up.
        """
        start = time.time()
        while time.time() - start < max_wait:
            result = self.health_check()
            if result.get('status') == 'healthy':
                elapsed = time.time() - start
                logger.info(f'Docling Serve is healthy (waited {elapsed:.1f}s)')
                return
            time.sleep(5)
        logger.warning(f'Docling Serve did not recover within {max_wait}s')


# ──────────────────────────────────────────────────────────────────────
# Element mapper
# ──────────────────────────────────────────────────────────────────────


def map_docling_to_elements(
    docling_response: dict[str, Any],
    filename: str,
) -> list[dict[str, Any]]:
    """Map an ``ExportDocumentResponse`` to Unstructured-compatible elements.

    Walks the ``DoclingDocument`` body tree in reading order, resolving
    ``$ref`` pointers to items in ``texts[]``, ``tables[]``, and
    ``pictures[]``. Groups (lists, chapters, sections) are recursed
    into but do not produce elements themselves.

    Args:
        docling_response: A single ``ExportDocumentResponse`` dict
            from Docling Serve containing ``json_content`` and
            optionally ``md_content``.
        filename: Original filename, attached to element metadata.

    Returns:
        A list of element dicts compatible with the Unstructured
        element schema.
    """
    elements: list[dict[str, Any]] = []
    # Docling can return a "success" result whose document is None when the
    # page pipeline errored (e.g. "Coordinate 'lower' is less than 'upper'",
    # "cannot write empty image", or a VLM stop_reason=length truncation). In
    # that case there is no parseable structure — return no elements rather
    # than raising AttributeError on None.get (RAG-1660: one such batch was
    # crashing the whole 638-page document despite other batches producing
    # hundreds of elements).
    if not docling_response:
        return elements
    json_content = docling_response.get('json_content') or {}

    # Build ref lookup: self_ref → item (from texts, tables, pictures)
    ref_lookup = _build_ref_lookup(json_content)

    # Walk body tree in reading order
    body = json_content.get('body', {})
    _walk_children(body, ref_lookup, filename, elements)

    return elements


def _build_ref_lookup(json_content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build map from ``$ref`` string to the actual item dict.

    Indexes items from ``texts``, ``tables``, ``pictures``,
    ``key_value_items``, and ``groups``. Groups are included so the
    tree walker can recurse into them.
    """
    lookup: dict[str, dict[str, Any]] = {}
    for collection in ('texts', 'tables', 'pictures', 'key_value_items'):
        for item in json_content.get(collection, []):
            ref = item.get('self_ref', '')
            if ref:
                lookup[ref] = item
    # Also include groups so we can recurse into them
    for group in json_content.get('groups', []):
        ref = group.get('self_ref', '')
        if ref:
            lookup[ref] = group
    return lookup


def _walk_children(
    node: dict[str, Any],
    ref_lookup: dict[str, dict[str, Any]],
    filename: str,
    elements: list[dict[str, Any]],
) -> None:
    """Recursively walk children in reading order, appending elements.

    Resolves each ``$ref`` in the node's ``children`` list against the
    lookup table. Recognized labels produce an element dict; groups
    and other container nodes are recursed into for their children.
    """
    for child_ref_obj in node.get('children', []):
        ref = child_ref_obj.get('$ref', '')
        child = ref_lookup.get(ref)
        if child is None:
            continue

        label = child.get('label', '')
        element_type = _LABEL_TO_TYPE.get(label)

        if element_type is not None:
            element = _make_element(child, element_type, filename)
            if element is not None:
                elements.append(element)

        # Recurse into groups (lists, chapters, sections)
        if child.get('children'):
            _walk_children(child, ref_lookup, filename, elements)


def _make_element(
    item: dict[str, Any],
    element_type: str,
    filename: str,
) -> dict[str, Any] | None:
    """Build a single Unstructured-compatible element dict.

    Returns ``None`` for non-Image elements that have no text content,
    since empty text elements carry no useful information for
    downstream chunking.
    """
    text = item.get('text', '') or item.get('orig', '') or ''
    page_number = _get_page_number(item)

    metadata: dict[str, Any] = {'filename': filename}
    if page_number is not None:
        metadata['page_number'] = page_number

    if element_type == 'Table':
        # Extract markdown representation as text
        text = _table_to_text(item)
        metadata['text_as_html'] = _table_to_html(item)

    elif element_type == 'Image':
        image_data = item.get('image', {})
        uri = image_data.get('uri', '')
        if uri.startswith('data:'):
            # Extract base64 data after the comma
            _, _, b64 = uri.partition(',')
            metadata['image_base64'] = b64
            mimetype = image_data.get('mimetype', '')
            metadata['filetype'] = mimetype

    if not text and element_type not in ('Image', 'PageBreak'):
        return None

    return {'type': element_type, 'text': text, 'metadata': metadata}


def _get_page_number(item: dict[str, Any]) -> int | None:
    """Extract page number from provenance.

    Docling items include a ``prov`` list with bounding-box and page
    information. We take the page number from the first provenance
    entry.
    """
    prov = item.get('prov', [])
    if prov and isinstance(prov, list) and len(prov) > 0:
        return prov[0].get('page_no')
    return None


def _table_to_text(table_item: dict[str, Any]) -> str:
    """Convert table cells to a markdown-style text representation.

    Builds a grid from the ``table_cells`` array and renders it as a
    pipe-delimited markdown table with a header separator row after
    the first row.
    """
    data = table_item.get('data', {})
    if not data:
        return ''

    num_rows = data.get('num_rows', 0)
    num_cols = data.get('num_cols', 0)
    if num_rows == 0 or num_cols == 0:
        return ''

    # Build grid
    grid = [[''] * num_cols for _ in range(num_rows)]
    for cell in data.get('table_cells', []):
        r = cell.get('start_row_offset_idx', 0)
        c = cell.get('start_col_offset_idx', 0)
        if r < num_rows and c < num_cols:
            grid[r][c] = cell.get('text', '')

    # Render markdown table
    lines = []
    for i, row in enumerate(grid):
        lines.append('| ' + ' | '.join(row) + ' |')
        if i == 0:
            lines.append('| ' + ' | '.join('---' for _ in row) + ' |')
    return '\n'.join(lines)


def _table_to_html(table_item: dict[str, Any]) -> str:
    """Convert table cells to an HTML table string.

    Respects ``column_header``, ``colspan``, and ``rowspan`` from the
    Docling table cell model. Header cells use ``<th>``, data cells
    use ``<td>``.
    """
    data = table_item.get('data', {})
    if not data:
        return ''

    num_rows = data.get('num_rows', 0)
    cells = data.get('table_cells', [])

    if not cells:
        return ''

    # Group cells by row
    rows: dict[int, list[dict[str, Any]]] = {}
    for cell in cells:
        r = cell.get('start_row_offset_idx', 0)
        rows.setdefault(r, []).append(cell)

    html_parts = ['<table>']
    for r in range(num_rows):
        html_parts.append('<tr>')
        row_cells = sorted(
            rows.get(r, []),
            key=lambda c: c.get('start_col_offset_idx', 0),
        )
        for cell in row_cells:
            tag = 'th' if cell.get('column_header', False) else 'td'
            colspan = cell.get('end_col_offset_idx', 1) - cell.get('start_col_offset_idx', 0)
            rowspan = cell.get('end_row_offset_idx', 1) - cell.get('start_row_offset_idx', 0)
            attrs = ''
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            text = cell.get('text', '')
            html_parts.append(f'<{tag}{attrs}>{text}</{tag}>')
        html_parts.append('</tr>')
    html_parts.append('</table>')
    return ''.join(html_parts)
