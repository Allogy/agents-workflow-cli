"""YAML serialization and deserialization for WDF workflow definitions.

Provides load_workflow_yaml() and dump_workflow_yaml() for converting
between YAML strings and WorkflowDefinition Pydantic models.

YAML handling lives in the CLI layer because PyYAML is a CLI dependency,
not a shared-models dependency.

Reference: RFC Section 4.1, Jira RAG-945.
"""

import yaml
from workflow_models.wdf.workflow import WorkflowDefinition


def load_workflow_yaml(yaml_str: str) -> WorkflowDefinition:
    """Parse a YAML string into a validated WorkflowDefinition.

    Args:
        yaml_str: Raw YAML content of a .workflow.yaml file.

    Returns:
        A validated WorkflowDefinition instance.

    Raises:
        yaml.YAMLError: If the YAML is malformed.
        pydantic.ValidationError: If the data fails schema validation.
    """
    data = yaml.safe_load(yaml_str)
    return WorkflowDefinition.model_validate(data)


def dump_workflow_yaml(workflow: WorkflowDefinition) -> str:
    """Serialize a WorkflowDefinition to a YAML string.

    Produces clean YAML output suitable for .workflow.yaml files:
    - Uses aliases ('from' instead of 'from_node')
    - Excludes None values
    - Excludes internal fields (parsed_config)

    Args:
        workflow: A validated WorkflowDefinition instance.

    Returns:
        A YAML string representation.
    """
    data = workflow.model_dump(by_alias=True, exclude_none=True)

    # Remove internal fields that shouldn't appear in YAML output.
    # parsed_config is an internal validation artifact on each node.
    for node_data in data.get('nodes', {}).values():
        node_data.pop('parsed_config', None)

    return yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
