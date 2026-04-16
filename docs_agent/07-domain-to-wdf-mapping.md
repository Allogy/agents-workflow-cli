# Domain Language to WDF Mapping

This document maps user-friendly descriptions to the correct WDF node types and pipeline patterns. Use this when translating requirements into workflow definitions.

## Input Method Mapping

| What the user says | WDF node type | Execution mode |
|---|---|---|
| "Type a question" / "enter text" / "describe something" | `plain_txt_input` | INPUT |
| "Fill out a form" / "provide details" / "select options" | `structured_input` | INPUT |
| "Upload a file" / "attach a document" / "submit a PDF" | `file_upload` | INPUT |

## Processing Type Mapping

| What the user says | WDF node type(s) | Notes |
|---|---|---|
| "Generate content" / "write a report" / "summarize" | `llm_call` | Use template with variable refs |
| "Extract data" / "pull out fields" / "parse the document" | `structured_output` | Requires a JSON schema |
| "Search documents then answer" / "use the knowledge base" | `retrieve` then `llm_call` | Two nodes: search + process |
| "Let the AI figure it out" / "agent handles it" | `agent` | Flexible, tool-using agent |
| "Search docs and let the AI handle it" | `rag_agent` | Agent with KB access |
| "Classify" / "categorize" / "sort into groups" | `llm_call` | Low temperature (0.0-0.1) |
| "Compare" / "analyze differences" | `llm_call` | Multiple inputs in template |

## Review Mapping

| What the user says | WDF action |
|---|---|
| "Someone should approve" / "needs review" / "human check" | Add `human_review` node before exit |
| "Deliver directly" / "no review needed" | No review node |

## Output Style Mapping

| What the user says | WDF exit node type |
|---|---|
| "A report" / "written response" / "summary" | `llm_call` as final node |
| "Structured data" / "specific fields" / "JSON output" | `structured_output` as final node |
| "Whatever the reviewer approves" | `human_review` as final node |

## Knowledge Base Mapping

| What the user says | WDF approach |
|---|---|
| "Search our documents" / "use the knowledge base" | Add `retrieve` node before LLM processing |
| "Use an AI agent with document access" | Use `rag_agent` instead of `agent` |
| "No documents needed" / "just AI reasoning" | Use `llm_call` or `agent` directly |

## Common Requirement-to-Pipeline Translations

### "I want to analyze uploaded documents"

```
file_upload -> llm_call (analyze) -> llm_call (report)
```

### "I want to answer questions using our docs"

```
plain_txt_input -> retrieve -> llm_call (answer)
```

### "I want a form that gets processed and reviewed"

```
structured_input -> llm_call (process) -> human_review
```

### "I want to classify customer tickets"

```
plain_txt_input -> llm_call (classify, temp=0.0)
```

### "I want to extract data from invoices and get approval"

```
file_upload -> llm_call (extract, temp=0.0) -> llm_call (classify) -> human_review
```

### "I want an AI agent to handle customer questions using our knowledge base"

```
plain_txt_input -> rag_agent
```

### "I want to collect research requirements, search multiple KBs, and generate a report"

```
structured_input -> retrieve -> llm_call (extract) -> llm_call (summarize)
```

### "I want to upload a document, find related context, and analyze it with review"

```
file_upload -> retrieve -> llm_call (analyze) -> human_review
```

## Structured Input Schema Patterns

### Simple text form
```yaml
schema:
  type: object
  properties:
    question:
      title: Your Question
      type: string
  required: [question]
```

### Selection with description
```yaml
schema:
  type: object
  properties:
    category:
      title: Category
      type: string
      enum: [Research, Analysis, Report, Summary]
    details:
      title: Details
      type: string
  required: [category, details]
```

### Multi-field professional form
```yaml
schema:
  type: object
  properties:
    title:
      title: Title
      type: string
    description:
      title: Description
      type: string
    priority:
      title: Priority
      type: string
      enum: [low, medium, high, critical]
    deadline:
      title: Deadline
      type: string
    tags:
      title: Tags
      type: array
      items:
        type: string
  required: [title, description, priority]
```
