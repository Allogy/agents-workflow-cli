"""Tests for the typed API exception hierarchy.

RAG-946: Phase 1 — API Client

BDD Scenario: Client handles errors gracefully
  Given the API returns a 4xx or 5xx error
  When any client method is called
  Then a typed exception is raised with the error detail
  And the HTTP status code is preserved
"""

import httpx
import pytest

from cli.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
    raise_for_status,
)


class TestExceptionHierarchy:
    """All typed exceptions inherit from APIError."""

    def test_api_error_is_base(self):
        assert issubclass(AuthenticationError, APIError)
        assert issubclass(AuthorizationError, APIError)
        assert issubclass(NotFoundError, APIError)
        assert issubclass(ValidationError, APIError)
        assert issubclass(ConflictError, APIError)
        assert issubclass(RateLimitError, APIError)
        assert issubclass(ServerError, APIError)

    def test_api_error_inherits_from_exception(self):
        assert issubclass(APIError, Exception)

    def test_api_error_carries_status_code_and_detail(self):
        err = APIError('test', status_code=418, detail='I am a teapot')
        assert err.status_code == 418
        assert err.detail == 'I am a teapot'
        assert str(err) == 'test'

    def test_rate_limit_error_carries_retry_after(self):
        err = RateLimitError('slow down', status_code=429, retry_after=60)
        assert err.retry_after == 60
        assert err.status_code == 429

    def test_rate_limit_error_retry_after_defaults_to_none(self):
        err = RateLimitError('slow down', status_code=429)
        assert err.retry_after is None


class TestRaiseForStatus:
    """raise_for_status maps HTTP status codes to typed exceptions."""

    def _make_response(self, status_code: int, json_body: dict | None = None) -> httpx.Response:
        """Create a minimal httpx.Response for testing."""
        content = b''
        headers = {}
        if json_body is not None:
            import json

            content = json.dumps(json_body).encode()
            headers['content-type'] = 'application/json'

        return httpx.Response(
            status_code=status_code,
            content=content,
            headers=headers,
            request=httpx.Request('GET', 'https://api.example.com/test'),
        )

    def test_success_does_not_raise(self):
        response = self._make_response(200)
        raise_for_status(response)  # Should not raise

    def test_201_does_not_raise(self):
        response = self._make_response(201)
        raise_for_status(response)

    def test_204_does_not_raise(self):
        response = self._make_response(204)
        raise_for_status(response)

    def test_400_raises_validation_error(self):
        response = self._make_response(400, {'detail': 'Invalid input'})
        with pytest.raises(ValidationError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == 'Invalid input'

    def test_401_raises_authentication_error(self):
        response = self._make_response(401, {'detail': 'Could not validate credentials'})
        with pytest.raises(AuthenticationError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == 'Could not validate credentials'

    def test_403_raises_authorization_error(self):
        response = self._make_response(403, {'detail': 'Access denied'})
        with pytest.raises(AuthorizationError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 403

    def test_404_raises_not_found_error(self):
        response = self._make_response(404, {'detail': 'Workflow not found'})
        with pytest.raises(NotFoundError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 404

    def test_409_raises_conflict_error(self):
        response = self._make_response(409, {'detail': 'Resource conflict'})
        with pytest.raises(ConflictError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 409

    def test_422_raises_validation_error(self):
        response = self._make_response(422, {'detail': 'Unprocessable'})
        with pytest.raises(ValidationError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 422

    def test_429_raises_rate_limit_error_with_retry_after(self):
        response = self._make_response(429, {'detail': 'Rate limit exceeded', 'retry_after': 60})
        with pytest.raises(RateLimitError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 60

    def test_429_raises_rate_limit_error_without_retry_after(self):
        response = self._make_response(429, {'detail': 'Rate limit exceeded'})
        with pytest.raises(RateLimitError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.retry_after is None

    def test_500_raises_server_error(self):
        response = self._make_response(500, {'detail': 'Internal server error'})
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 500

    def test_502_raises_server_error(self):
        response = self._make_response(502)
        with pytest.raises(ServerError):
            raise_for_status(response)

    def test_503_raises_server_error(self):
        response = self._make_response(503, {'detail': 'Service temporarily unavailable'})
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 503

    def test_unknown_4xx_raises_api_error(self):
        response = self._make_response(418, {'detail': 'I am a teapot'})
        with pytest.raises(APIError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.status_code == 418
        assert not isinstance(exc_info.value, ServerError)

    def test_error_without_json_body(self):
        response = httpx.Response(
            status_code=500,
            content=b'Internal Server Error',
            headers={'content-type': 'text/plain'},
            request=httpx.Request('GET', 'https://api.example.com/test'),
        )
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(response)
        assert 'status 500' in str(exc_info.value)

    def test_nested_detail_format(self):
        """Backend invitation conflict returns {"detail": {"detail": "...", ...}}."""
        response = self._make_response(
            409,
            {'detail': {'detail': 'A pending invitation already exists', 'id': '123'}},
        )
        with pytest.raises(ConflictError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.detail == 'A pending invitation already exists'

    def test_agui_error_format(self):
        """AG-UI middleware returns {"error": {"type": "...", "message": "..."}}."""
        response = self._make_response(
            404,
            {'error': {'type': 'WORKFLOWNOTFOUNDERROR', 'message': 'Workflow not found'}},
        )
        with pytest.raises(NotFoundError) as exc_info:
            raise_for_status(response)
        assert exc_info.value.detail == 'Workflow not found'

    def test_error_preserves_status_code(self):
        """Status code is always preserved on the exception instance."""
        for code in (400, 401, 403, 404, 409, 422, 429, 500, 502, 503):
            response = self._make_response(code, {'detail': 'test'})
            with pytest.raises(APIError) as exc_info:
                raise_for_status(response)
            assert exc_info.value.status_code == code
