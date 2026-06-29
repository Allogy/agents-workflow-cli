# Variable References and Output Paths

Variable references let nodes access data produced by upstream nodes. The syntax is `{{slug.output.field}}` where `slug` is the node's key in the YAML, and `field` is a specific output path.

## Syntax Rules

1. Always use double curly braces: `{{slug.output.field}}`
2. The `.output.` segment is required between the slug and the field
3. You must specify a field — using `{{slug.output}}` alone will fail at runtime
4. The referenced slug must be an upstream node (connected before this node in the edge graph)
5. References work inside `template`, `searchQuery`, `primaryInput`, and other string config fields

## Output Paths by Node Type

### plain_txt_input

| Path | Type | Description |
|------|------|-------------|
| `output.text` | string | The text the user entered |

Example: `{{user_input.output.text}}`

### structured_input

| Path | Type | Description |
|------|------|-------------|
| `output.formData` | object | All form field values as an object |
| `output.formData.fieldName` | any | A specific form field value (replace `fieldName` with the actual property name from the schema) |

Example: `{{form.output.formData.name}}` or `{{form.output.formData.priority}}`

Note: The field names come from the `properties` in the `schema` definition. If your schema defines `name` and `email`, you access them as `{{slug.output.formData.name}}` and `{{slug.output.formData.email}}`.

### file_upload

| Path | Type | Description |
|------|------|-------------|
| `output.text` | string | Extracted text from all uploaded files (combined) |
| `output.files` | array | Array of uploaded file metadata |
| `output.files[0].name` | string | First file's name |
| `output.files[0].file_id` | string | First file's UUID |
| `output.files[0].s3_uri` | string | S3 storage location |
| `output.files[0].size` | number | File size in bytes |
| `output.files[0].content_type` | string | MIME type |
| `output.files[0].text` | string | Extracted text from first file |

Most common: `{{upload.output.text}}` to get the text content of uploaded documents.

### llm_call

| Path | Type | Description |
|------|------|-------------|
| `output.text` | string | The generated text response |

Example: `{{summarizer.output.text}}`

### agent

| Path | Type | Description |
|------|------|-------------|
| `output.response` | string | The agent's response text |

Example: `{{my_agent.output.response}}`

### rag_agent

| Path | Type | Description |
|------|------|-------------|
| `output.response` | string | The RAG agent's response text |
| `output.documents` | array | Retrieved documents from the knowledge base |
| `output.documents[0].content` | string | First document's content |
| `output.documents[0].score` | number | First document's relevance score |

Example: `{{rag.output.response}}`

### retrieve

| Path | Type | Description |
|------|------|-------------|
| `output.results` | array | Array of search results |
| `output.results[0].content` | string | First result's content |
| `output.results[0].score` | number | First result's relevance score |
| `output.results[0].metadata` | object | First result's metadata |

Example: `{{search.output.results}}`

Important: RETRIEVE does NOT have `output.text`. Always use `output.results`.

### structured_output

| Path | Type | Description |
|------|------|-------------|
| `output.structured` | object | The validated structured data matching the schema |

Example: `{{extract.output.structured}}`

### human_review

| Path | Type | Description |
|------|------|-------------|
| `output.approved` | boolean | Whether the reviewer approved |
| `output.feedback` | string | Reviewer's feedback text |
| `output.comments` | string | Review comments |

Example: `{{review.output.feedback}}`

### api_consumption

When `saveToMemory` is `false` (default), the parsed response body is available inline. When `saveToMemory` is `true`, the response body is written to the run memory scope and the node exposes file metadata instead.

| Path | Type | Description |
|------|------|-------------|
| `output.memory_file_path` | string | Relative path of the saved response under the run memory scope (only when `saveToMemory` is true). |
| `output.memory_file_url` | string | Signed download URL for the saved response file. |
| `output.content_type` | string | MIME type of the HTTP response. |
| `output.size_bytes` | number | Size of the response body in bytes. |
| `output.status_code` | integer | HTTP status code returned by the API. |

Example: `{{fetch_transcript.output.memory_file_path}}` feeds a downstream `memory_file_url` node; `{{fetch_transcript.output.memory_file_url}}` gives a ready-to-use signed URL.

## Common Patterns

### API response saved to memory, then linked

```yaml
fetch_transcript:
  type: api_consumption
  execution_mode: MESSAGES
  config:
    connectorId: zoom-api
    saveToMemory: true
    memoryFilePath: "transcripts/{{trigger.output.meeting_uuid}}.vtt"

transcript_url:
  type: memory_file_url
  execution_mode: OUTPUT
  config:
    path: "{{fetch_transcript.output.memory_file_path}}"
```

### Chaining LLM calls

```yaml
template: |
  Based on this analysis:
  {{analyze.output.text}}

  Generate a report with actionable recommendations.
```

### Form data into an LLM prompt

```yaml
template: |
  Name: {{form.output.formData.name}}
  Topic: {{form.output.formData.topic}}

  Write a summary about the above topic.
```

### KB retrieval into an LLM prompt

```yaml
template: |
  Context from knowledge base:
  {{search.output.results}}

  Question: {{user_input.output.text}}

  Answer the question using only the provided context.
```

### File content into an LLM prompt

```yaml
template: |
  Document content:
  {{upload.output.text}}

  Extract the key findings from this document.
```
