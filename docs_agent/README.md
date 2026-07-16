# WDF documentation (agents & authors)

Human-readable reference for the **Workflow Definition Format** (`.workflow.yaml`). The Pydantic source of truth lives in `shared-models/src/workflow_models/wdf/`; YAML load/dump is in the CLI (`cli.wdf_yaml`).

## Read order

| # | Document | Purpose |
|---|----------|---------|
| 1 | [WDF syntax reference](./01-wdf-syntax-reference.md) | Top-level YAML shape, slugs, edges, execution modes, validation overview |
| 2 | [Node types reference](./02-node-types-reference.md) | All 12 schema types (11 CLI-supported) with config fields |
| 3 | [Variable references](./03-variable-references.md) | `{{slug.output.field}}` paths per node type |
| 4 | [Example workflows](./04-example-workflows.md) | Annotated patterns and sample graphs |
| 5 | [Best practices](./05-best-practices-and-generation-rules.md) | Generation rules for agents authoring YAML |
| 6 | [Deployment & CLI](./06-deployment-and-cli-commands.md) | `validate`, `push`, `run`, credentials |
| 7 | [Domain → WDF mapping](./07-domain-to-wdf-mapping.md) | Translate business requirements into node choices |

## Related docs

- CLI command details: [`../docs/validate-command.md`](../docs/validate-command.md), [`../docs/push-command.md`](../docs/push-command.md)
- Example YAML files: [`../shared-models/examples/`](../shared-models/examples/)
- Frontend `design:` block (Designer layout): [`../../frontend/docs/workflow/designer/wdf-design-extension.md`](../../frontend/docs/workflow/designer/wdf-design-extension.md)

## Quick facts

- **12** node types in the WDF schema; **11** supported by `workflow validate` / `push` / `run`
- **CLI unsupported:** `document_extraction` (schema-valid, fails validate check 10)
- **13** validation checks (checks 11–13 need registry data; use `--offline` to skip them)
