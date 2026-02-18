"""Built-in workflow templates for 'workflow init'.

Provides a registry of template metadata and functions to list and load
templates from YAML files bundled with the package.

Templates are stored as .workflow.yaml files in this directory.

Reference: Jira RAG-948
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Directory containing the template YAML files
TEMPLATES_DIR = Path(__file__).parent


@dataclass(frozen=True)
class TemplateInfo:
    """Metadata for a built-in workflow template."""

    name: str
    description: str
    nodes: str  # Short summary of the node pipeline


# Registry of available templates with metadata.
# Order determines display order in --list and interactive picker.
TEMPLATES: dict[str, TemplateInfo] = {
    'simple-form': TemplateInfo(
        name='simple-form',
        description='Form input -> Agent processing',
        nodes='structured_input, agent',
    ),
    'text-to-agent': TemplateInfo(
        name='text-to-agent',
        description='Plain text -> Agent',
        nodes='plain_txt_input, agent',
    ),
    'document-analysis': TemplateInfo(
        name='document-analysis',
        description='File upload -> Vector search -> LLM analysis',
        nodes='file_upload, retrieve, llm_call',
    ),
    'form-with-review': TemplateInfo(
        name='form-with-review',
        description='Form -> LLM -> Human approval gate',
        nodes='structured_input, llm_call, human_review',
    ),
    'batch-processing': TemplateInfo(
        name='batch-processing',
        description='Text input -> LLM processing pipeline',
        nodes='plain_txt_input, llm_call, llm_call',
    ),
    'rag-qa': TemplateInfo(
        name='rag-qa',
        description='Question -> Vector search -> LLM answer',
        nodes='plain_txt_input, retrieve, llm_call',
    ),
    'blank': TemplateInfo(
        name='blank',
        description='Empty workflow with just entry/exit',
        nodes='plain_txt_input',
    ),
}


def list_templates() -> list[TemplateInfo]:
    """Return all available templates in display order."""
    return list(TEMPLATES.values())


def get_template_info(name: str) -> TemplateInfo | None:
    """Look up template metadata by name. Returns None if not found."""
    return TEMPLATES.get(name)


def load_template_yaml(name: str) -> str:
    """Load the raw YAML content for a template.

    Args:
        name: Template name (e.g. 'rag-qa').

    Returns:
        The raw YAML string content of the template file.

    Raises:
        KeyError: If the template name is not in the registry.
        FileNotFoundError: If the template YAML file is missing.
    """
    if name not in TEMPLATES:
        raise KeyError(f'Unknown template: {name!r}')

    template_path = TEMPLATES_DIR / f'{name}.workflow.yaml'
    if not template_path.exists():
        raise FileNotFoundError(f'Template file not found: {template_path}')

    return template_path.read_text()
