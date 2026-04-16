# Example Workflows

Complete, working WDF YAML examples. Each demonstrates a different pipeline pattern. Use these as templates when generating new workflows.

## Pattern 1: Text Input to Agent

The simplest pattern. User types text, an agent processes it.

Pipeline: `plain_txt_input -> agent`

```yaml
name: Text to Agent
description: Plain text input routed directly to an agent
version: 1
tags:
  - agent

nodes:
  user_input:
    type: plain_txt_input
    execution_mode: INPUT
    label: Enter Your Message
    config:
      placeholder: Type your message here...

  agent:
    type: agent
    execution_mode: MESSAGES
    label: Assistant Agent
    config:
      agent_name: YOUR_AGENT_NAME
      primaryInput: "{{user_input.output.text}}"

edges:
  - from: user_input
    to: agent

entry: user_input
exit: agent
```

## Pattern 2: Form Input to Agent

User fills a structured form, an agent processes it.

Pipeline: `structured_input -> agent`

```yaml
name: Simple Form Workflow
description: Structured form input routed to an agent for processing
version: 1
tags:
  - form

nodes:
  form_input:
    type: structured_input
    execution_mode: INPUT
    label: User Form
    config:
      schema:
        type: object
        properties:
          request:
            title: Request
            type: string
          context:
            title: Additional Context
            type: string
        required:
          - request

  agent:
    type: agent
    execution_mode: MESSAGES
    label: Processing Agent
    config:
      agent_name: YOUR_AGENT_NAME
      primaryInput: "{{form_input.output.text}}"

edges:
  - from: form_input
    to: agent

entry: form_input
exit: agent
```

## Pattern 3: LLM Processing Pipeline

Text input processed through multiple LLM calls in sequence.

Pipeline: `plain_txt_input -> llm_call -> llm_call`

```yaml
name: Batch Processing Pipeline
description: Text input processed through an LLM pipeline
version: 1
tags:
  - processing

nodes:
  text_input:
    type: plain_txt_input
    execution_mode: INPUT
    label: Enter Items
    config:
      placeholder: "Enter items to process (one per line)..."

  llm_process:
    type: llm_call
    execution_mode: MESSAGES
    label: Process Items
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.3
      maxTokens: 4096
      template: |
        Process each of the following items individually:

        {{text_input.output.text}}

        For each item, provide:
        - Item: the original text
        - Result: your processed output
        - Status: success or needs_review

  llm_summarize:
    type: llm_call
    execution_mode: MESSAGES
    label: Summarize Results
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.3
      maxTokens: 2048
      template: |
        Summarize the batch processing results below:

        {{llm_process.output.text}}

        Include total items processed, success rate, and any items
        that need further review.

edges:
  - from: text_input
    to: llm_process
  - from: llm_process
    to: llm_summarize

entry: text_input
exit: llm_summarize
```

## Pattern 4: Form with Human Review

User submits a form, LLM processes it, a human reviews before finalizing.

Pipeline: `structured_input -> llm_call -> human_review`

```yaml
name: Form with Human Review
description: Form input processed by LLM, then routed for human approval
version: 1
tags:
  - form
  - review

nodes:
  form_input:
    type: structured_input
    execution_mode: INPUT
    label: Submit Request
    config:
      schema:
        type: object
        properties:
          title:
            title: Request Title
            type: string
          description:
            title: Description
            type: string
          priority:
            title: Priority
            type: string
            enum: [low, medium, high]
        required: [title, description]

  llm_process:
    type: llm_call
    execution_mode: MESSAGES
    label: Process Request
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.5
      maxTokens: 2048
      template: |
        Review the following request and provide a structured assessment:

        Title: {{form_input.output.formData.title}}
        Description: {{form_input.output.formData.description}}

        Provide:
        1. A brief summary
        2. Risk assessment
        3. Recommended action

  human_review:
    type: human_review
    execution_mode: FLOW
    label: Manager Approval
    config:
      review_prompt: >
        Review the LLM assessment and the original request.
        Approve to proceed or reject to send back for revision.
      timeoutMinutes: 1440
      allowApprove: true
      allowReject: true
      allowEdit: false

edges:
  - from: form_input
    to: llm_process
  - from: llm_process
    to: human_review

entry: form_input
exit: human_review
```

## Pattern 5: RAG Question Answering

User asks a question, relevant documents are retrieved from a knowledge base, LLM generates a grounded answer.

Pipeline: `plain_txt_input -> retrieve -> llm_call`

```yaml
name: RAG Question & Answer
description: Question answering with retrieval-augmented generation
version: 1
tags:
  - rag
  - qa

nodes:
  question:
    type: plain_txt_input
    execution_mode: INPUT
    label: Ask a Question
    config:
      placeholder: Enter your question...

  retrieve:
    type: retrieve
    execution_mode: FLOW
    label: Search Knowledge Base
    config:
      knowledge_base_name: YOUR_KNOWLEDGE_BASE_NAME
      topK: 5
      scoreThreshold: 0.5

  answer:
    type: llm_call
    execution_mode: MESSAGES
    label: Generate Answer
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.3
      maxTokens: 2048
      system_prompt: >
        You are a helpful assistant that answers questions based on
        the provided context. If the context doesn't contain enough
        information, say so clearly.
      template: |
        Context from knowledge base:
        {{retrieve.output.results}}

        Question: {{question.output.text}}

        Provide a clear, accurate answer based on the context above.

edges:
  - from: question
    to: retrieve
  - from: retrieve
    to: answer

entry: question
exit: answer
```

## Pattern 6: Document Upload with Analysis

User uploads a file, documents are retrieved for context, LLM analyzes the content.

Pipeline: `file_upload -> retrieve -> llm_call`

```yaml
name: Document Analysis
description: Upload a document, retrieve related context, and analyze
version: 1
tags:
  - document
  - rag

nodes:
  file_upload:
    type: file_upload
    execution_mode: INPUT
    label: Upload Document
    config:
      acceptedFormats: [pdf, docx, txt]
      maxFileSize: 10485760

  retrieve:
    type: retrieve
    execution_mode: FLOW
    label: Search Knowledge Base
    config:
      knowledge_base_name: YOUR_KNOWLEDGE_BASE_NAME
      topK: 5
      scoreThreshold: 0.5

  llm_analysis:
    type: llm_call
    execution_mode: MESSAGES
    label: Analyze Document
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.3
      maxTokens: 4096
      template: |
        Analyze the following document content:
        {{file_upload.output.text}}

        Using the following related context:
        {{retrieve.output.results}}

        Provide a comprehensive analysis including key findings,
        important details, and any recommendations.

edges:
  - from: file_upload
    to: retrieve
  - from: retrieve
    to: llm_analysis

entry: file_upload
exit: llm_analysis
```

## Pattern 7: Document Processing with Extraction and Review

Upload a document, extract structured data, classify it, and route for human review.

Pipeline: `file_upload -> llm_call -> llm_call -> human_review`

```yaml
name: Invoice Processing Pipeline
description: Extract data from uploaded invoices and route for approval
version: 1
tags:
  - finance
  - document-processing

nodes:
  upload:
    type: file_upload
    execution_mode: INPUT
    label: Upload Invoice
    config:
      acceptedFormats: [pdf, png, jpg]
      maxFileSize: 10485760

  extract:
    type: llm_call
    execution_mode: MESSAGES
    label: Extract Invoice Fields
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.0
      maxTokens: 1024
      template: |
        Extract the following fields from this invoice document:
        {{upload.output.text}}

        Return a JSON object with these fields:
        - vendor_name (string, required)
        - invoice_number (string, required)
        - total_amount (number, required)
        - line_items (array of objects, optional)

        Respond with only the JSON object.

  classify:
    type: llm_call
    execution_mode: MESSAGES
    label: Classify Invoice
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.0
      maxTokens: 200
      template: |
        Given the following invoice data:
        {{extract.output.text}}

        Classify this invoice into one of: SUPPLIES, SERVICES, EQUIPMENT, OTHER.
        Respond with only the category name.

  review:
    type: human_review
    execution_mode: FLOW
    label: Manager Approval
    config:
      review_prompt: >
        Review the extracted invoice data and classification.
        Approve if data looks correct, reject to re-process.
      timeoutMinutes: 1440
      allowApprove: true
      allowReject: true
      allowEdit: false

edges:
  - from: upload
    to: extract
  - from: extract
    to: classify
  - from: classify
    to: review

entry: upload
exit: review
```

## Pattern 8: Multi-KB Retrieval Pipeline

Form input drives searches across multiple knowledge bases, with LLM extraction and summarization.

Pipeline: `structured_input -> retrieve -> llm_call -> llm_call -> structured_output`

```yaml
name: Retrieval Pipeline
description: Form input, vector search, content extraction, LLM summarization
version: 1
tags:
  - retrieve
  - llm-extraction

nodes:
  search_form:
    type: structured_input
    execution_mode: INPUT
    label: Search Query
    config:
      schema:
        type: object
        properties:
          query:
            title: Search Query
            type: string
          max_results:
            title: Max Results
            type: integer
            default: 5
        required: [query]

  vector_search:
    type: retrieve
    execution_mode: FLOW
    label: Vector Search
    config:
      knowledge_base_name: YOUR_KNOWLEDGE_BASE_NAME
      topK: 5
      scoreThreshold: 0.7
      includeMetadata: true

  content_extractor:
    type: llm_call
    execution_mode: MESSAGES
    label: Content Extractor
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.0
      maxTokens: 2048
      template: |
        Extract and organize the key content from these retrieved documents:
        {{vector_search.output.results}}

        Include any tables, structured data, and important details.

  summarizer:
    type: llm_call
    execution_mode: MESSAGES
    label: Summarizer
    config:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      temperature: 0.5
      maxTokens: 2048
      template: |
        Based on the extracted content, provide a comprehensive summary:
        {{content_extractor.output.text}}

edges:
  - from: search_form
    to: vector_search
  - from: vector_search
    to: content_extractor
  - from: content_extractor
    to: summarizer

entry: search_form
exit: summarizer
```
