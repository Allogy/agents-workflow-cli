"""Shared pytest fixtures and configuration for workflow CLI tests."""

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.fixture()
def runner() -> CliRunner:
    """Provide a Typer CLI test runner."""
    return CliRunner()


@pytest.fixture()
def cli_invoke(runner: CliRunner):
    """Provide a helper to invoke the CLI app."""

    def _invoke(*args: str):
        return runner.invoke(app, list(args))

    return _invoke
