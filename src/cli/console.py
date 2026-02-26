"""Shared console factory for the CLI.

Provides a ``get_console()`` factory that respects the global ``--no-color``
flag set by the Typer callback in ``cli.main``.  Extracted into its own module
to avoid circular imports (main -> commands -> main).
"""

from __future__ import annotations

from rich.console import Console

# Set by the Typer callback in cli.main
_no_color: bool = False


def set_no_color(value: bool) -> None:
    """Update the global no-color flag (called by the Typer callback)."""
    global _no_color  # noqa: PLW0603
    _no_color = value


def get_console() -> Console:
    """Return a Rich Console respecting the global --no-color flag.

    Rich Console natively honours the NO_COLOR environment variable,
    so only the explicit CLI flag needs handling here.
    """
    return Console(no_color=True) if _no_color else Console()
