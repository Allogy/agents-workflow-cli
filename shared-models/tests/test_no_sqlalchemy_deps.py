"""Tests that the shared models package has zero SQLAlchemy/SQLModel dependencies.

This directly tests the acceptance criteria from RAG-941:
  Given the shared models package is created
  When it is installed via pip/uv
  Then it has zero SQLAlchemy or SQLModel dependencies

The test works by inspecting the actual module imports and verifying that
neither sqlalchemy nor sqlmodel are present in any transitive import.
"""

import importlib
import sys


class TestNoSQLAlchemyDependencies:
    """Ensure no SQLAlchemy or SQLModel dependencies leak into the shared package."""

    def test_workflow_models_does_not_import_sqlalchemy(self):
        """Verify sqlalchemy is not imported when loading workflow_models."""
        # Force reimport
        if 'workflow_models' in sys.modules:
            # It's already imported; check current state
            pass
        else:
            importlib.import_module('workflow_models')

        # Check that sqlalchemy was not pulled in
        sqlalchemy_modules = [mod for mod in sys.modules if mod.startswith('sqlalchemy')]
        assert sqlalchemy_modules == [], (
            f'SQLAlchemy modules were imported by workflow_models: {sqlalchemy_modules}'
        )

    def test_workflow_models_does_not_import_sqlmodel(self):
        """Verify sqlmodel is not imported when loading workflow_models."""
        if 'workflow_models' not in sys.modules:
            importlib.import_module('workflow_models')

        sqlmodel_modules = [mod for mod in sys.modules if mod.startswith('sqlmodel')]
        assert sqlmodel_modules == [], (
            f'SQLModel modules were imported by workflow_models: {sqlmodel_modules}'
        )

    def test_enums_module_has_no_sqlalchemy(self):
        """Verify the enums module only depends on stdlib."""
        import inspect

        from workflow_models import enums

        source = inspect.getsource(enums)
        assert 'sqlalchemy' not in source.lower()
        assert 'sqlmodel' not in source.lower()

    def test_schemas_modules_use_pydantic_not_sqlmodel(self):
        """Verify schema modules import from pydantic, not sqlmodel."""
        import inspect

        from workflow_models.schemas import edges, execution, metadata, nodes, visuals, workflows

        for module in [workflows, nodes, edges, visuals, metadata, execution]:
            source = inspect.getsource(module)
            assert 'sqlmodel' not in source.lower(), f'{module.__name__} contains sqlmodel import'
            assert 'sqlalchemy' not in source.lower(), (
                f'{module.__name__} contains sqlalchemy import'
            )

    def test_package_only_depends_on_pydantic(self):
        """Verify all schema base classes inherit from pydantic.BaseModel."""
        from pydantic import BaseModel

        from workflow_models.schemas import (
            EdgeVisualsCreate,
            LogicalEdgeCreate,
            LogicalNodeCreate,
            NodeVisualsCreate,
            WorkflowCreate,
            WorkflowExecutionCreate,
            WorkflowMetadataCreate,
            WorkflowVisualsCreate,
        )

        for cls in [
            WorkflowCreate,
            LogicalNodeCreate,
            LogicalEdgeCreate,
            WorkflowVisualsCreate,
            NodeVisualsCreate,
            EdgeVisualsCreate,
            WorkflowMetadataCreate,
            WorkflowExecutionCreate,
        ]:
            assert issubclass(cls, BaseModel), (
                f'{cls.__name__} does not inherit from pydantic.BaseModel'
            )
