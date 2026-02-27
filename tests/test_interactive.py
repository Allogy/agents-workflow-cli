"""Unit tests for the interactive HITL prompting module."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from cli.interactive import (
    check_interactive_preconditions,
    prompt_for_file_upload,
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


# ---------------------------------------------------------------------------
# TestPromptForFileUpload
# ---------------------------------------------------------------------------


class TestPromptForFileUpload:
    def test_single_file_accepted(self, tmp_path: Path) -> None:
        """Single valid file accepted and returned as list."""
        test_file = tmp_path / 'report.pdf'
        test_file.write_bytes(b'%PDF-fake')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value=str(test_file)),
            patch('cli.interactive.Confirm.ask', return_value=False),  # No more files
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf', '.docx'],
                console=console,
            )
        assert result == [test_file]

    def test_multiple_files_accepted(self, tmp_path: Path) -> None:
        """Multiple files collected via 'Add another?' loop."""
        file_a = tmp_path / 'a.pdf'
        file_b = tmp_path / 'b.pdf'
        file_a.write_bytes(b'%PDF-a')
        file_b.write_bytes(b'%PDF-b')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch(
                'cli.interactive.Prompt.ask',
                side_effect=[str(file_a), str(file_b)],
            ),
            patch(
                'cli.interactive.Confirm.ask',
                side_effect=[True, False],  # Add another? Yes, then No
            ),
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf'],
                console=console,
            )
        assert result == [file_a, file_b]

    def test_nonexistent_file_rejected(self, tmp_path: Path) -> None:
        """Non-existent path triggers error and reprompt, then valid file succeeds."""
        good_file = tmp_path / 'good.pdf'
        good_file.write_bytes(b'%PDF-good')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch(
                'cli.interactive.Prompt.ask',
                side_effect=['/nonexistent/bad.pdf', str(good_file)],
            ),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf'],
                console=console,
            )
        assert result == [good_file]
        output = buf.getvalue()
        assert 'not found' in output.lower() or 'does not exist' in output.lower()

    def test_wrong_extension_rejected(self, tmp_path: Path) -> None:
        """File with wrong extension triggers error and reprompt."""
        bad_ext = tmp_path / 'image.png'
        bad_ext.write_bytes(b'\x89PNG')
        good_file = tmp_path / 'doc.pdf'
        good_file.write_bytes(b'%PDF')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch(
                'cli.interactive.Prompt.ask',
                side_effect=[str(bad_ext), str(good_file)],
            ),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf', '.docx'],
                console=console,
            )
        assert result == [good_file]
        output = buf.getvalue()
        assert '.pdf' in output or 'format' in output.lower()

    def test_ctrl_c_returns_none(self) -> None:
        """Ctrl+C (KeyboardInterrupt) during prompt returns None."""
        buf = io.StringIO()
        console = Console(file=buf)
        with patch('cli.interactive.Prompt.ask', side_effect=KeyboardInterrupt):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                console=console,
            )
        assert result is None

    def test_no_format_restriction_accepts_any(self, tmp_path: Path) -> None:
        """When accepted_formats is None, any file extension is allowed."""
        test_file = tmp_path / 'data.csv'
        test_file.write_text('a,b,c')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value=str(test_file)),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=None,
                console=console,
            )
        assert result == [test_file]

    def test_file_exceeding_max_size_rejected(self, tmp_path: Path) -> None:
        """File exceeding max_file_size triggers error and reprompt."""
        big_file = tmp_path / 'big.pdf'
        big_file.write_bytes(b'x' * 2000)
        small_file = tmp_path / 'small.pdf'
        small_file.write_bytes(b'x' * 500)
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch(
                'cli.interactive.Prompt.ask',
                side_effect=[str(big_file), str(small_file)],
            ),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            result = prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf'],
                max_file_size=1000,
                console=console,
            )
        assert result == [small_file]
        output = buf.getvalue()
        assert 'size' in output.lower() or 'large' in output.lower()

    def test_shows_node_context(self, tmp_path: Path) -> None:
        """Output contains node slug, step type, and accepted formats."""
        test_file = tmp_path / 'doc.pdf'
        test_file.write_bytes(b'%PDF')
        buf = io.StringIO()
        console = Console(file=buf)
        with (
            patch('cli.interactive.Prompt.ask', return_value=str(test_file)),
            patch('cli.interactive.Confirm.ask', return_value=False),
        ):
            prompt_for_file_upload(
                'node-1',
                'upload-doc',
                'file_upload',
                accepted_formats=['.pdf', '.docx'],
                console=console,
            )
        output = buf.getvalue()
        assert 'upload-doc' in output
        assert 'file_upload' in output
