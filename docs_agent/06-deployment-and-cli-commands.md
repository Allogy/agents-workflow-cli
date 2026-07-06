# Deployment and CLI Commands

How to validate, deploy, and run WDF workflows using the Workflow CLI.

## Prerequisites

- Python 3.13+ with `uv` package manager
- CLI installed: `uv tool install agents-workflow-cli`
- API credentials configured in `~/.workflow/config.yaml`:

```yaml
host: https://api.sb.allogy.com
api_key: your-api-key
org_id: your-org-uuid
```

Or via environment variables:

```bash
export WORKFLOW_API_HOST=https://api.sb.allogy.com
export WORKFLOW_API_KEY=your-api-key
export WORKFLOW_ORG_ID=your-org-uuid
# Optional: user JWT sent as a Bearer token for endpoints that reject API-key auth.
export WORKFLOW_JWT=your-jwt-access-token
```

## Step 1: Save the YAML

Save the generated workflow as a `.workflow.yaml` file:

```
my-workflow.workflow.yaml
```

The `.workflow.yaml` extension is required.

## Step 2: Validate

Check the workflow for errors before deploying:

```bash
workflow validate my-workflow.workflow.yaml
```

This runs 13 checks: YAML syntax, schema conformance, graph structure, variable references, and registry validation. Fix any errors before proceeding.

## Step 3: Deploy

Push the workflow to the platform:

```bash
workflow push my-workflow.workflow.yaml
```

First push creates the workflow. Subsequent pushes update it in place (idempotent). A `.workflow.lock` file is created to track server-side UUIDs.

If the workflow references agents or knowledge bases by name, the push command resolves them to UUIDs automatically.

## Step 4: Run

Execute the deployed workflow:

```bash
# Basic polling mode
workflow run "My Workflow Name"

# Real-time streaming
workflow run "My Workflow Name" --stream

# Interactive mode (prompts for input/review inline)
workflow run "My Workflow Name" --stream --interactive

# With initial input data
workflow run "My Workflow Name" --input '{"text": "Hello"}'

# Fire and forget (start and exit immediately)
workflow run "My Workflow Name" --no-follow
```

## Step 5: Interact with Paused Workflows

When a workflow pauses at an input or review node:

```bash
# Check current status
workflow status

# Submit text input to a paused node
workflow input --node-id <node-id> --data '{"text": "my answer"}'

# Approve a human review
workflow review --run-id <id> --node-id <id> --approve

# Reject with feedback
workflow review --run-id <id> --node-id <id> --reject --comment "Needs revision"
```

## Other Useful Commands

```bash
# List all workflows in the organization
workflow list

# Pull a workflow from the platform to a local YAML file
workflow pull "Workflow Name" -o local-copy.workflow.yaml

# Delete a workflow
workflow delete "Workflow Name" --force

# Scaffold from a built-in template
workflow init --list                    # See available templates
workflow init --template rag-qa         # Create from template
```

## Lockfile

After pushing, a `.workflow.lock` file is created alongside the YAML. It tracks the server-side UUIDs for idempotent updates. Commit this file to version control alongside the YAML.

## Customizing a Deployed Workflow

1. Edit the `.workflow.yaml` file
2. Run `workflow validate` to check changes
3. Run `workflow push` to update the deployed version

The lockfile ensures the same workflow is updated rather than creating a duplicate.
