# Node Types Reference

There are 10 active node types available for building workflows. Each has a specific purpose, execution mode, and config schema.

## Input Nodes

### plain_txt_input

Pauses the workflow and asks the user for free-form text.

- **Execution mode:** INPUT
- **When to use:** The user provides a question, description, or any unstructured text.

```yaml
user_input:
  type: plain_txt_input
  execution_mode: INPUT
  label: Ask a Question
  config:
    placeholder: Type your question here...
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `placeholder` | string | No | Hint text shown in the input field |

### structured_input

Pauses the workflow and presents a form with typed fields.

- **Execution mode:** INPUT
- **When to use:** You need specific, structured data from the user (names, selections, numbers).

```yaml
form_input:
  type: structured_input
  execution_mode: INPUT
  label: Customer Details
  config:
    schema:
      type: object
      properties:
        name:
          title: Full Name
          type: string
        priority:
          title: Priority Level
          type: string
          enum: [low, medium, high]
      required: [name]
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema` | object | Yes | JSON Schema defining the form fields. Use `properties` for fields, `required` for mandatory ones, `enum` for dropdowns, `title` for display labels. |

### file_upload

Pauses the workflow and asks the user to upload one or more files.

- **Execution mode:** INPUT
- **When to use:** The workflow processes documents, images, or data files.

```yaml
upload_doc:
  type: file_upload
  execution_mode: INPUT
  label: Upload Document
  config:
    acceptedFormats: [pdf, docx, txt, csv]
    maxFileSize: 10485760
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `acceptedFormats` | list[string] | Yes | Allowed file extensions (e.g., pdf, docx, csv) |
| `maxFileSize` | integer | Yes | Maximum file size in bytes (10485760 = 10 MB) |
| `saveToMemory` | boolean | No | When `true`, uploaded files are stored in the RLM sandbox memory bucket (`RLM_SANDBOX_MEMORY_BUCKET_NAME`) instead of the default workflow inputs bucket. Defaults to `false`. |

## Processing Nodes

### llm_call

Sends a prompt to an LLM and returns the generated text.

- **Execution mode:** MESSAGES
- **When to use:** You need AI-generated text — summaries, analysis, classification, reports.

```yaml
summarize:
  type: llm_call
  execution_mode: MESSAGES
  label: Summarize Content
  config:
    model: us.anthropic.claude-sonnet-4-20250514-v1:0
    temperature: 0.3
    maxTokens: 2048
    system_prompt: You are a helpful assistant.
    template: |
      Summarize the following content:
      {{user_input.output.text}}

      Provide a concise summary in 3-5 bullet points.
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | LLM model identifier. Use `us.anthropic.claude-sonnet-4-20250514-v1:0` for Claude Sonnet. |
| `template` | string | Yes | The prompt template. Use `{{slug.output.field}}` for variable references. |
| `system_prompt` | string | No | System-level instructions for the LLM. |
| `temperature` | float | No | 0.0 (deterministic) to 2.0 (creative). Default 0.7. Use 0.0-0.3 for extraction/classification, 0.3-0.5 for reports, 0.5-0.7 for creative content. |
| `maxTokens` | integer | No | Maximum tokens in the response. 1024 for short outputs, 2048-4096 for reports, 8192 for long-form. |

### agent

Delegates processing to a registered platform agent. Agents have their own system prompts, tools, and capabilities.

- **Execution mode:** MESSAGES
- **When to use:** You want a pre-configured AI agent to handle the task flexibly, potentially using tools.

```yaml
support_agent:
  type: agent
  execution_mode: MESSAGES
  label: Customer Support Agent
  config:
    agent_name: Customer Support Agent
    primaryInput: "{{user_input.output.text}}"
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_name` | string | Yes* | Name of the platform agent to invoke. Resolved to UUID during push. |
| `primaryInput` | string | No | Variable reference for the input to send to the agent. |
| `model` | string | No | Override the agent's default model. |
| `temperature` | float | No | Override the agent's default temperature. |
| `maxTokens` | integer | No | Override the agent's default max tokens. |
| `system_prompt` | string | No | Override the agent's default system prompt. |

*Either `agent_name` or `agentId` (UUID) is required.

### rag_agent

A retrieval-augmented agent that searches knowledge bases before responding.

- **Execution mode:** MESSAGES
- **When to use:** You need an agent that draws on your documents/knowledge to answer questions.

```yaml
qa_agent:
  type: rag_agent
  execution_mode: MESSAGES
  label: Knowledge Base Agent
  config:
    agent_name: Research Agent
    knowledge_base_names:
      - Company Policies
      - Industry Standards
    primaryInput: "{{user_input.output.text}}"
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_name` | string | Yes* | Name of the platform agent. |
| `knowledge_base_names` | list[string] | Yes* | Knowledge base names to search. Resolved to UUIDs during push. |
| `primaryInput` | string | No | Variable reference for input routing. |
| `topK` | integer | No | Number of documents to retrieve per KB. |
| `system_prompt` | string | No | Override the agent's system prompt. |

### retrieve

Performs vector/semantic search against a knowledge base. Returns matching documents.

- **Execution mode:** FLOW
- **When to use:** You need to fetch relevant documents from a knowledge base before processing them with an LLM.

```yaml
search_kb:
  type: retrieve
  execution_mode: FLOW
  label: Search Knowledge Base
  config:
    knowledge_base_name: Company Policies
    topK: 5
    scoreThreshold: 0.5
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `knowledge_base_name` | string | Yes* | KB name. Resolved to UUID during push. |
| `topK` | integer | No | Number of results to return (default 5). |
| `searchQuery` | string | No | Custom search query. Can use variable references. |
| `scoreThreshold` | float | No | Minimum relevance score (0.0-1.0). |
| `enableReranking` | boolean | No | Enable result reranking (default false). |
| `includeMetadata` | boolean | No | Include document metadata (default true). |

### structured_output

Sends a prompt to an LLM and validates the response against a JSON Schema.

- **Execution mode:** OUTPUT
- **When to use:** You need the AI to produce specific structured data (JSON with defined fields).

```yaml
extract_fields:
  type: structured_output
  execution_mode: OUTPUT
  label: Extract Invoice Fields
  config:
    model: us.anthropic.claude-sonnet-4-20250514-v1:0
    schema:
      type: object
      properties:
        vendor_name:
          type: string
        total_amount:
          type: number
        line_items:
          type: array
          items:
            type: string
      required: [vendor_name, total_amount]
    primaryInput: "{{upload.output.text}}"
    system_prompt: Extract the requested fields from the document.
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema` | object | Yes | JSON Schema defining the expected output structure. |
| `model` | string | No | LLM model identifier. |
| `primaryInput` | string | No | Variable reference for input data. |
| `system_prompt` | string | No | Instructions for the extraction. |

## Human Interaction Nodes

### human_review

Pauses the workflow for a human to approve, reject, or request revision.

- **Execution mode:** FLOW
- **When to use:** A person needs to check the AI's work before proceeding.

```yaml
manager_review:
  type: human_review
  execution_mode: FLOW
  label: Manager Approval
  config:
    review_prompt: >
      Review the generated report. Approve if accurate,
      reject if fundamentally wrong.
    timeoutMinutes: 1440
    allowApprove: true
    allowReject: true
    allowEdit: false
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `review_prompt` | string | No | Instructions shown to the reviewer. |
| `timeoutMinutes` | integer | No | How long to wait before timing out (1440 = 24 hours). |
| `allowApprove` | boolean | No | Enable approve action (default true). |
| `allowReject` | boolean | No | Enable reject action (default true). |
| `allowEdit` | boolean | No | Enable inline editing before approval (default false). |

## Integration Nodes

### api_consumption

Calls an external HTTP API through a configured org-scoped API Connector. The connector (referenced by `connectorId`) carries the OpenAPI schema, variable definitions, host allowlist, and secrets on the backend.

- **Execution mode:** MESSAGES
- **When to use:** The workflow needs live data from a third-party API, or needs to download a large response body (a transcript, export, or media file) into the run's memory for downstream nodes.

```yaml
fetch_transcript:
  type: api_consumption
  execution_mode: MESSAGES
  label: Download Zoom Transcript
  config:
    connectorId: zoom-api
    primaryInput: "{{zoom_trigger.output.text}}"
    operationHint: getMeetingTranscript
    timeoutSeconds: 60
    saveToMemory: true
    memoryFilePath: "transcripts/{{zoom_trigger.output.meeting_uuid}}.vtt"
```

Config fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `connectorId` | string | Yes | UUID of the org-scoped API Connector to invoke. |
| `primaryInput` | string | No | Variable reference for the input routed to the connector. |
| `maxRecursionDepth` | integer | No | Max follow-up API calls the node may chain (default 1). |
| `operationHint` | string | No | Name of the connector operation to prefer. |
| `timeoutSeconds` | integer | No | Per-request timeout in seconds. |
| `saveToMemory` | boolean | No | When `true`, stream the HTTP response body to a file in the run's memory scope instead of parsing it inline. Defaults to `false`. |
| `memoryFilePath` | string | No | Templated, path-confined relative path under the run memory scope (e.g. `transcripts/{{trigger.output.meeting_uuid}}.vtt`). Only used when `saveToMemory` is `true`. Defaults to `api/{node_id}/response.<ext>` when omitted. Must be relative — absolute paths and `..` segments are rejected. |

When `saveToMemory` is `true`, the node exposes the response as a memory file rather than inline text. Feed the resulting file into a downstream `memory_file_url` node to produce a signed download URL:

```yaml
transcript_url:
  type: memory_file_url
  execution_mode: OUTPUT
  label: Transcript Download Link
  config:
    path: "{{fetch_transcript.output.memory_file_path}}"
```

See `03-variable-references.md` for the `output.memory_file_path`, `output.memory_file_url`, `output.content_type`, `output.size_bytes`, and `output.status_code` paths exposed by this node.
