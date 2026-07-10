"""Tests for local API response models."""

from __future__ import annotations

from cli.client import SubmitInputResponse, SubmitReviewResponse


def test_submit_input_response_accepts_run_id() -> None:
    """Input resume responses include run_id when the backend returns it."""
    resp = SubmitInputResponse.model_validate(
        {
            'workflow_id': 'wf-1',
            'run_id': 'run-1',
            'node_id': 'node-1',
            'status': 'ok',
            'message': 'submitted',
        }
    )

    assert resp.run_id == 'run-1'


def test_submit_input_response_run_id_defaults_to_none() -> None:
    """Older input resume responses without run_id still validate."""
    resp = SubmitInputResponse.model_validate(
        {
            'workflow_id': 'wf-1',
            'node_id': 'node-1',
            'status': 'ok',
            'message': 'submitted',
        }
    )

    assert resp.run_id is None


def test_submit_review_response_accepts_run_id() -> None:
    """Review resume responses include run_id when the backend returns it."""
    resp = SubmitReviewResponse.model_validate(
        {
            'workflow_id': 'wf-1',
            'run_id': 'run-1',
            'status': 'ok',
            'message': 'submitted',
        }
    )

    assert resp.run_id == 'run-1'


def test_submit_review_response_run_id_defaults_to_none() -> None:
    """Older review resume responses without run_id still validate."""
    resp = SubmitReviewResponse.model_validate(
        {
            'workflow_id': 'wf-1',
            'status': 'ok',
            'message': 'submitted',
        }
    )

    assert resp.run_id is None
