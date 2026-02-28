# workflow init — Template Scaffolding

Create new workflow files from built-in templates.

## Usage

```bash
# List available templates
workflow init --list

# Create from a template (interactive)
workflow init

# Create from a specific template
workflow init --template rag-qa

# Specify output filename
workflow init --template simple-form -o my-workflow.workflow.yaml

# Overwrite existing file
workflow init --template blank -o existing.workflow.yaml --force
```

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--template NAME` | `-t` | Template name to scaffold from |
| `--output PATH` | `-o` | Output file path (default: `{template-name}.workflow.yaml`) |
| `--force` | | Overwrite existing file if it exists |
| `--list` | | List all available templates and exit |

## Available Templates

### simple-form
**Pattern:** `structured_input → agent`

A structured form collects user input, then an agent processes it. Good starting point for data collection workflows.

**Nodes:** structured_input, agent

---

### text-to-agent
**Pattern:** `plain_txt_input → agent`

The simplest agent workflow: free-text input processed directly by an agent. Minimal starting point.

**Nodes:** plain_txt_input, agent

---

### document-analysis
**Pattern:** `file_upload → retrieve → llm_call`

Upload a document, retrieve related context from a knowledge base, and analyze with an LLM. Classic RAG pipeline for document processing.

**Nodes:** file_upload, retrieve, llm_call

---

### form-with-review
**Pattern:** `structured_input → llm_call → human_review`

Structured input processed by an LLM, then routed to a human reviewer for approval. Good for workflows requiring manual oversight.

**Nodes:** structured_input, llm_call, human_review

---

### batch-processing
**Pattern:** `plain_txt_input → llm_call → llm_call`

Text input processed through a multi-stage LLM pipeline. First LLM processes items, second summarizes results.

**Nodes:** plain_txt_input, llm_call, llm_call

---

### rag-qa
**Pattern:** `plain_txt_input → retrieve → llm_call`

Classic RAG question-answering: user asks a question, relevant docs are retrieved, and an LLM generates a grounded answer.

**Nodes:** plain_txt_input, retrieve, llm_call

---

### blank
**Pattern:** `plain_txt_input` (minimal)

Empty workflow with just an entry/exit node. A minimal starting point to build from scratch.

**Nodes:** plain_txt_input

---

## Examples

### Quick Start
```bash
# See what's available
workflow init --list

# Create a RAG Q&A workflow
workflow init --template rag-qa

# Output: rag-qa.workflow.yaml created
```

### Custom Filename
```bash
workflow init --template document-analysis -o invoice-processor.workflow.yaml
```

### Overwrite Protection
```bash
# First time - succeeds
workflow init --template blank -o test.workflow.yaml

# Second time - fails with error
workflow init --template blank -o test.workflow.yaml
# Error: File already exists: test.workflow.yaml
# Use --force to overwrite.

# Third time with --force - succeeds
workflow init --template blank -o test.workflow.yaml --force
```

## Template Validation

All built-in templates are guaranteed to pass `workflow validate`:

```bash
# Create and validate in one go
workflow init --template rag-qa
workflow validate rag-qa.workflow.yaml
# All checks passed ✓
```

## Template Customization

After scaffolding, customize the generated `.workflow.yaml` file:

1. **Update placeholder values**: Replace `YOUR_KNOWLEDGE_BASE_ID`, agent IDs, etc.
2. **Adjust parameters**: Tune `temperature`, `maxTokens`, `topK`, etc.
3. **Modify structure**: Add/remove nodes, change edge connections
4. **Validate changes**: Run `workflow validate` after edits

## Integration with Other Commands

```bash
# Scaffold → Validate → Push
workflow init --template rag-qa -o my-qa.workflow.yaml
workflow validate my-qa.workflow.yaml
workflow push my-qa.workflow.yaml
```

## Reference

- **Jira Ticket**: RAG-948
- **Related Commands**: `workflow validate`
- **Template Location**: `src/cli/templates/`
- **WDF Schema**: See `docs/validate-command.md` for node types and validation rules
