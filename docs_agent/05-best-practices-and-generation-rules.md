# Best Practices and Generation Rules

Guidance for generating valid, high-quality WDF workflow YAML files.

## Linear Pipeline Rules

When generating workflows, follow these rules for reliable output:

1. **Use only STATIC edges** — connect nodes in a straight line, no branching
2. **One entry, one exit** — `entry` is the first node, `exit` is the last
3. **Every node must be reachable** from the entry via edges
4. **No cycles** — edges must not create loops
5. **Edge `from`/`to` must reference existing node slugs**

## Node Slug Naming

- Use `snake_case`: `upload_invoice`, `extract_fields`, `generate_report`
- Be descriptive: `analyze_sentiment` is better than `llm_1`
- Keep slugs short but meaningful
- Slugs must be unique within the workflow

## Choosing the Right Node Type

| User Need | Node Type |
|-----------|-----------|
| User types free text | `plain_txt_input` |
| User fills a form with specific fields | `structured_input` |
| User uploads a document or file | `file_upload` |
| AI generates text (summaries, reports, analysis) | `llm_call` |
| AI extracts structured data (JSON with defined fields) | `structured_output` |
| AI searches a knowledge base for relevant documents | `retrieve` |
| A pre-configured AI agent handles the task | `agent` |
| An AI agent with knowledge base access handles the task | `rag_agent` |
| A human reviews and approves/rejects | `human_review` |

## Model Selection

Use `us.anthropic.claude-sonnet-4-20250514-v1:0` as the default model for `llm_call` and `structured_output` nodes. It provides a good balance of quality and speed.

## Temperature Guidelines

| Task Type | Temperature | Rationale |
|-----------|-------------|-----------|
| Data extraction, classification | 0.0 - 0.1 | Deterministic, factual output needed |
| YAML or code generation | 0.1 - 0.2 | Low variance for valid syntax |
| Reports, summaries | 0.3 - 0.5 | Some flexibility for natural prose |
| Creative content, brainstorming | 0.5 - 0.7 | More varied and creative output |
| Conversational agents | 0.7 | Natural, engaging responses |

## maxTokens Guidelines

| Output Length | maxTokens |
|---------------|-----------|
| Short answers, classifications | 200 - 512 |
| Paragraphs, summaries | 1024 - 2048 |
| Full reports, long-form content | 2048 - 4096 |
| Complete documents, YAML generation | 4096 - 8192 |

## Writing LLM Templates

Templates are the prompt text sent to the LLM. They use variable references to include upstream data.

### Do:
- Start with clear instructions about what the LLM should produce
- Include the upstream data via `{{slug.output.field}}`
- Specify the desired output format (bullet points, JSON, prose, etc.)
- End with a clear call to action

### Don't:
- Reference node slugs that don't exist in the workflow
- Use `{{slug.output}}` without a field name
- Reference nodes that aren't upstream (not connected before this node)
- Write prompts without including the relevant input data

### Template Example:

```yaml
template: |
  Analyze the following customer feedback:
  {{upload_feedback.output.text}}

  Perform:
  1. Sentiment analysis (positive/neutral/negative per response)
  2. Theme extraction (group feedback into recurring topics)
  3. Rank themes by frequency

  Output a structured analysis with clear sections for each finding.
```

## Structured Input Schema Design

When defining form fields for `structured_input` nodes:

- Use `title` for human-readable labels
- Use `enum` for dropdown/selection fields
- Use `required` to list mandatory fields
- Keep forms focused (3-6 fields per form)
- Use appropriate types: `string`, `integer`, `number`, `boolean`, `array`

```yaml
schema:
  type: object
  properties:
    topic:
      title: What topic should we cover?
      type: string
    difficulty:
      title: Difficulty Level
      type: string
      enum: [beginner, intermediate, advanced]
    include_examples:
      title: Include practical examples?
      type: boolean
  required: [topic, difficulty]
```

## Knowledge Base References

When a workflow uses knowledge bases:

- Use `knowledge_base_name` (singular) in `retrieve` nodes
- Use `knowledge_base_names` (list) in `rag_agent` nodes
- Names are resolved to UUIDs automatically during deployment
- Use `YOUR_KNOWLEDGE_BASE_NAME` as a placeholder when the actual name is unknown

## Human Review Placement

Add `human_review` nodes when:
- AI-generated content will be shared externally
- Decisions have real-world consequences
- Quality assurance is required before the next step
- Users want a checkpoint to verify accuracy

Place review nodes after the AI processing but before the final output or delivery step.

## Common Pipeline Patterns

| Pattern | Pipeline | Use Case |
|---------|----------|----------|
| Simple Q&A | `text_input -> llm_call` | Quick AI responses |
| Agent routing | `text_input -> agent` | Flexible AI processing |
| RAG Q&A | `text_input -> retrieve -> llm_call` | Knowledge-grounded answers |
| Form + processing | `structured_input -> llm_call` | Structured data processing |
| Document analysis | `file_upload -> llm_call` | File content analysis |
| Review gate | `input -> llm_call -> human_review` | Human oversight |
| Multi-step report | `input -> llm_call -> llm_call` | Sequential AI processing |
| Full pipeline | `file_upload -> retrieve -> llm_call -> human_review` | Document processing with context and review |

## Validation Checklist

Before considering a generated workflow complete, verify:

- [ ] Every node has a valid `type` and `execution_mode`
- [ ] All edges reference existing node slugs
- [ ] `entry` and `exit` reference existing node slugs
- [ ] Variable references use valid output paths for each node type
- [ ] LLM nodes have `model` and `template` fields
- [ ] Input nodes have appropriate config (placeholder, schema, acceptedFormats)
- [ ] No orphaned nodes (all reachable from entry via edges)
- [ ] No cycles in the edge graph
- [ ] Tags are descriptive lowercase strings
