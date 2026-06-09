"""Configuration for selecting and constructing a document parser.

``ParserConfig`` decouples the shared factory from any one consumer's
configuration mechanism. The backend builds it from its Pydantic
``Settings`` singleton; the kb-ingest-pipeline builds it from environment
variables. The shared package depends on neither.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ParserConfig:
    """Inputs that determine which document parser the factory returns.

    Attributes:
        docling_serve_url: Base URL of a Docling Serve instance. When set
            (non-empty), the factory returns a Docling adapter; otherwise
            it falls back to Unstructured. This is the toggle between the
            two backends.
        unstructured_api_url: Base URL of the legacy Unstructured API,
            used only when ``docling_serve_url`` is empty.
        docling_vlm_pipeline_preset: When set, the Docling adapter uses
            Docling's VLM pipeline with this preset (e.g. ``bedrock-proxy``)
            instead of the local CPU ``standard`` pipeline. The preset must
            exist on the Docling Serve instance.
        docling_page_batch_size: When > 0, PDFs are converted in page-range
            batches of this size so a single un-renderable page only voids
            its own batch rather than the whole document. 0 disables
            batching (one whole-document job).
        docling_max_parse_seconds: Hard wall-clock cap (seconds) for a single
            document's parse across ALL Docling retry attempts. Bounds the
            blast radius of a wedged parse (e.g. the 2026-06-08 ``.xlsx``
            hang) so one bad file cannot consume the whole ingest budget by
            being re-submitted on each retry. Defaults to 1800 (30 min).
    """

    docling_serve_url: str | None = None
    unstructured_api_url: str = 'http://unstructured:8000'
    docling_vlm_pipeline_preset: str | None = None
    docling_page_batch_size: int = 0
    docling_max_parse_seconds: int = 1800
