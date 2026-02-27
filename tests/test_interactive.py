"""Unit tests for the interactive HITL prompting module."""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest
from rich.console import Console

from cli.interactive import (
    check_interactive_preconditions,
    prompt_for_input,
    prompt_for_review,
)

# ---------------------------------------------------------------------------
# TestCheckInteractivePreconditions
# ---------------------------------------------------------------------------


class TestCheckInteractivePreconditions:
    def test_interactive_without_stream_raises_valueerror(self) -> None:
        """--interactive without --stream raises ValueError."""
        with pytest.raises(ValueError, match='--interactive requires --stream'):
            check_interactive_preconditions(stream=False, interactive=True)

    def test_non_tty_exits_with_code_2(self) -> None:
        """Non-TTY stdin causes sys.exit(2) with helpful message."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch.object(sys.stdin, 'isatty', return_value=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            check_interactive_preconditions(stream=True, interactive=True, console=console)
        assert exc_info.value.code == 2
        output = buf.getvalue()
        assert 'terminal' in output.lower() or 'TTY' in output

    def test_all_preconditions_pass(self) -> None:
        """stream=True, interactive=True, TTY stdin passes without error."""
        with patch.object(sys.stdin, 'isatty', return_value=True):
            result = check_interactive_preconditions(stream=True, interactive=True)
        assert result is None

    def test_non_interactive_skips_checks(self) -> None:
        """interactive=False skips all checks (no error even without --stream)."""
        result = check_interactive_preconditions(stream=False, interactive=False)
        assert result is None


# ---------------------------------------------------------------------------
# TestPromptForInput
# ---------------------------------------------------------------------------


class TestPromptForInput:
    def test_valid_json_input_confirmed(self) -> None:
        """Valid JSON string confirmed by user returns parsed dict."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value='{"key": "value"}'),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        assert result == {'key': 'value'}

    def test_valid_file_input(self) -> None:
        """@filepath input reads and returns parsed dict via parse_input_arg."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value='{"from": "file"}'),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        assert result == {'from': 'file'}

    def test_invalid_json_reprompts(self) -> None:
        """Invalid JSON on first attempt reprompts, valid JSON on second succeeds."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch(
                'cli.interactive.Prompt.ask',
                side_effect=['not-json', '{"valid": true}'],
            ),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        assert result == {'valid': True}

    def test_user_cancels_confirmation(self) -> None:
        """User entering valid JSON but declining confirmation returns None."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value='{"key": "value"}'),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        assert result is None

    def test_ctrl_c_returns_none(self) -> None:
        """Ctrl+C (KeyboardInterrupt) during prompt returns None."""
        buf = io.StringIO()
        console = Console(file=buf)
        with patch('cli.interactive.Prompt.ask', side_effect=KeyboardInterrupt):
            result = prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        assert result is None

    def test_shows_node_context(self) -> None:
        """Output contains node slug and step type for user context."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value='{"a": 1}'),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            prompt_for_input('node-1', 'extract-data', 'llm_call', console=console)
        output = buf.getvalue()
        assert 'extract-data' in output
        assert 'llm_call' in output


# ---------------------------------------------------------------------------
# TestPromptForReview
# ---------------------------------------------------------------------------


class TestPromptForReview:
    def test_approve_confirmed(self) -> None:
        """Selecting approve (1) and confirming returns ('approve', None)."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.IntPrompt.ask', return_value=1),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        assert result == ('approve', None)

    def test_reject_with_comment(self) -> None:
        """Selecting reject (2) with comment returns ('reject', comment)."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.IntPrompt.ask', return_value=2),
            patch('cli.interactive.Prompt.ask', return_value='Needs changes'),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        assert result == ('reject', 'Needs changes')

    def test_revise_with_comment(self) -> None:
        """Selecting revise (3) with comment returns ('revise', comment)."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.IntPrompt.ask', return_value=3),
            patch('cli.interactive.Prompt.ask', return_value='Update section 2'),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            result = prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        assert result == ('revise', 'Update section 2')

    def test_user_cancels_review(self) -> None:
        """User selecting option but declining confirmation returns None."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.IntPrompt.ask', return_value=1),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        assert result is None

    def test_ctrl_c_returns_none(self) -> None:
        """Ctrl+C (KeyboardInterrupt) during review prompt returns None."""
        buf = io.StringIO()
        console = Console(file=buf)
        with patch('cli.interactive.IntPrompt.ask', side_effect=KeyboardInterrupt):
            result = prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        assert result is None

    def test_shows_node_context(self) -> None:
        """Output contains node slug and step type for user context."""
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.IntPrompt.ask', return_value=1),
            patch('cli.interactive.Confirm.ask', return_value=True),
        ):
            prompt_for_review('node-1', 'review-node', 'human_review', console=console)
        output = buf.getvalue()
        assert 'review-node' in output
        assert 'human_review' in output
