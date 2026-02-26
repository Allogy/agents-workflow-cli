"""Unit tests for .last_run context file I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from cli.last_run import LastRunContext, load_last_run, save_last_run

SAMPLE_CONTEXT = LastRunContext(
    workflow_id=UUID('939843a8-6257-4475-bfc0-f7d6500d9f00'),
    run_id='a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    instance='https://stage.sb.allogy.com',
    started_at=datetime(2026, 2, 25, 10, 30, 0, tzinfo=UTC),
)


class TestSaveLastRun:
    def test_creates_file(self, tmp_path: Path) -> None:
        """save_last_run writes a .workflow.last_run file."""
        save_last_run(tmp_path, SAMPLE_CONTEXT)
        path = tmp_path / '.workflow.last_run'
        assert path.exists()

    def test_file_contains_workflow_id(self, tmp_path: Path) -> None:
        """Written file contains the workflow_id."""
        save_last_run(tmp_path, SAMPLE_CONTEXT)
        content = (tmp_path / '.workflow.last_run').read_text()
        assert '939843a8-6257-4475-bfc0-f7d6500d9f00' in content

    def test_file_contains_run_id(self, tmp_path: Path) -> None:
        """Written file contains the run_id."""
        save_last_run(tmp_path, SAMPLE_CONTEXT)
        content = (tmp_path / '.workflow.last_run').read_text()
        assert 'a1b2c3d4-e5f6-7890-abcd-ef1234567890' in content

    def test_file_has_header_comment(self, tmp_path: Path) -> None:
        """Written file starts with a header comment."""
        save_last_run(tmp_path, SAMPLE_CONTEXT)
        content = (tmp_path / '.workflow.last_run').read_text()
        assert content.startswith('# Auto-generated')


class TestLoadLastRun:
    def test_roundtrip(self, tmp_path: Path) -> None:
        """save then load returns equivalent data."""
        save_last_run(tmp_path, SAMPLE_CONTEXT)
        loaded = load_last_run(tmp_path)
        assert loaded is not None
        assert loaded.workflow_id == SAMPLE_CONTEXT.workflow_id
        assert loaded.run_id == SAMPLE_CONTEXT.run_id
        assert loaded.instance == SAMPLE_CONTEXT.instance

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """load_last_run returns None when file doesn't exist."""
        loaded = load_last_run(tmp_path)
        assert loaded is None

    def test_malformed_file_returns_none(self, tmp_path: Path) -> None:
        """load_last_run returns None for files with missing keys."""
        path = tmp_path / '.workflow.last_run'
        path.write_text('run_id: some-id\n')
        loaded = load_last_run(tmp_path)
        assert loaded is None

    def test_invalid_uuid_returns_none(self, tmp_path: Path) -> None:
        """load_last_run returns None when workflow_id is not a valid UUID."""
        path = tmp_path / '.workflow.last_run'
        path.write_text(
            'workflow_id: not-a-uuid\n'
            'run_id: some-id\n'
            'instance: https://example.com\n'
            'started_at: "2026-02-25T10:30:00+00:00"\n'
        )
        loaded = load_last_run(tmp_path)
        assert loaded is None


class TestLastRunOverwrite:
    def test_last_run_overwrite_latest_wins(self, tmp_path: Path) -> None:
        """Overwriting .last_run silently replaces with latest run -- latest run wins."""
        first = LastRunContext(
            workflow_id=UUID('939843a8-6257-4475-bfc0-f7d6500d9f00'),
            run_id='first-run-id',
            instance='https://stage.sb.allogy.com',
            started_at=datetime(2026, 2, 25, 10, 30, 0, tzinfo=UTC),
        )
        second = LastRunContext(
            workflow_id=UUID('939843a8-6257-4475-bfc0-f7d6500d9f00'),
            run_id='second-run-id',
            instance='https://stage.sb.allogy.com',
            started_at=datetime(2026, 2, 25, 11, 0, 0, tzinfo=UTC),
        )

        save_last_run(tmp_path, first)
        save_last_run(tmp_path, second)

        loaded = load_last_run(tmp_path)
        assert loaded is not None
        assert loaded.run_id == 'second-run-id'
