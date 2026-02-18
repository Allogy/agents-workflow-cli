"""Variable reference extraction for WDF templates.

Extracts {{slug.output.field}} Mustache-style variable references from
config strings and nested data structures. These references link nodes
together by slug and are validated against the workflow's node graph.

Reference: RFC Section 4.1, Jira RAG-945.
"""

import re
from dataclasses import dataclass
from typing import Any

# Matches {{slug.path.to.field}} — slug can contain word chars and hyphens.
_VAR_REF_PATTERN = re.compile(r'\{\{([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_.]+)\}\}')


@dataclass(frozen=True)
class VariableRef:
    """A parsed variable reference from a config template.

    Attributes:
        slug: The node slug being referenced (e.g. 'extract').
        path: The dot-separated path after the slug (e.g. 'output.extractedData').
        raw: The full reference string (e.g. 'extract.output.extractedData').
    """

    slug: str
    path: str

    @property
    def raw(self) -> str:
        return f'{self.slug}.{self.path}'


def extract_variable_refs(value: Any) -> list[VariableRef]:
    """Extract all variable references from a config value.

    Recursively traverses strings, dicts, and lists to find all
    {{slug.path}} patterns. Non-string leaf values are safely skipped.

    Args:
        value: A string, dict, list, or any other value to search.

    Returns:
        A list of VariableRef objects found in the value.
    """
    refs: list[VariableRef] = []
    _extract_recursive(value, refs)
    return refs


def _extract_recursive(value: Any, refs: list[VariableRef]) -> None:
    """Recursively extract variable references from nested structures."""
    if isinstance(value, str):
        for match in _VAR_REF_PATTERN.finditer(value):
            refs.append(VariableRef(slug=match.group(1), path=match.group(2)))
    elif isinstance(value, dict):
        for v in value.values():
            _extract_recursive(v, refs)
    elif isinstance(value, list):
        for item in value:
            _extract_recursive(item, refs)
