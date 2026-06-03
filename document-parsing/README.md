# agents-document-parsing

Shared document-parsing abstraction for the Capillary Actions platform,
published to CodeArtifact as `agents-document-parsing`.

This package is the **single source of truth** for how the platform turns
uploaded documents into chunks during **knowledge-base ingestion**. Two
deployables consume it:

- the backend (`agents-runner`) — local KB ingestion paths, and
- the standalone `kb-ingest-pipeline` image — Step Functions chunking tasks.

Previously each maintained its own copy of the parsing adapters, and they
drifted (one emitted DocTags, the other did not). Centralizing here makes
that class of bug impossible.

> Scope: this package covers **KB-ingestion document parsing only**. It is
> not involved in agent chat file uploads or workflow `FILE_UPLOAD` nodes
> (those use MarkItDown), nor in query-time retrieval (vector search over
> already-indexed chunks).

## Public API

```python
from document_parsing import (
    DocumentParserPort,          # adapter interface (Protocol)
    ParserConfig,                # config value object the factory consumes
    create_document_parser,      # returns the configured adapter
    DoclingServeDocumentParser,  # Docling Serve adapter (DocTags + VLM + page batching)
    UnstructuredDocumentParser,  # legacy Unstructured adapter
    DoclingConversionError,      # terminal Docling conversion failure
    HybridChunker,               # DocTags-aware chunker (lazy; needs docling-core)
)
```

### Selecting an adapter

The factory takes an explicit `ParserConfig` so it depends on neither the
backend's Pydantic `Settings` nor the kb-ingest-pipeline's environment
variables. Each consumer maps its own configuration into a `ParserConfig`:

```python
parser = create_document_parser(
    ParserConfig(
        docling_serve_url='http://docling-serve:5001',  # empty -> Unstructured fallback
        unstructured_api_url='http://unstructured:8000',
        docling_vlm_pipeline_preset='bedrock-proxy',     # None -> standard pipeline
        docling_page_batch_size=50,                      # 0 -> single whole-document job
    )
)
elements = parser.parse(file_bytes, filename)
doctags = parser.last_doctags  # '' when Unstructured / no DocTags
```

When `docling_serve_url` is empty/unset the factory returns the Unstructured
adapter — a safe, reversible per-environment toggle.

### HybridChunker

`HybridChunker` consumes Docling DocTags and emits the same grouped-chunk
dicts that `ContextualChunker` produces, so the existing context-generation
and embedding pipeline runs downstream unchanged. It requires `docling-core`
and is imported lazily (accessing `document_parsing.HybridChunker` triggers
the import); Unstructured-only consumers never pay that cost.

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check . && uv run ruff format .
```

Consumers install this package from CodeArtifact in CI/Docker. For local
development they use an editable install:

- backend (uv): `[tool.uv.sources]` editable path entry.
- kb-ingest-pipeline (pip): `pip install -e ../workflow-cli/document-parsing`.

## Publishing

```bash
make codeartifact-publish-document-parsing   # from the repo root
```

Bump `version` in `pyproject.toml` before publishing, and **publish before
building consumer images** — Docker builds resolve this package from
CodeArtifact, so the version must exist there first.
