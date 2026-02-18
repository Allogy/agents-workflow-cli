"""Typed API exceptions for the Workflow CLI client.

Maps HTTP status codes from the platform API to a typed exception hierarchy,
preserving the status code and error detail for programmatic handling.

Exception Hierarchy:
    APIError (base)
    ├── AuthenticationError   (401)
    ├── AuthorizationError    (403)
    ├── NotFoundError         (404)
    ├── ValidationError       (400, 422)
    ├── ConflictError         (409)
    ├── RateLimitError        (429)
    └── ServerError           (500, 502, 503)
"""

from __future__ import annotations

import httpx


class APIError(Exception):
    """Base exception for all platform API errors.

    Attributes:
        status_code: The HTTP status code returned by the API.
        detail: Human-readable error detail from the API response.
    """

    def __init__(self, message: str, *, status_code: int, detail: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class AuthenticationError(APIError):
    """Raised when the API returns 401 Unauthorized.

    Typically caused by a missing or invalid API key.
    """


class AuthorizationError(APIError):
    """Raised when the API returns 403 Forbidden.

    The API key is valid but lacks permission for the requested operation.
    """


class NotFoundError(APIError):
    """Raised when the API returns 404 Not Found.

    The requested resource does not exist.
    """


class ValidationError(APIError):
    """Raised when the API returns 400 Bad Request or 422 Unprocessable Entity.

    The request payload failed server-side validation.
    """


class ConflictError(APIError):
    """Raised when the API returns 409 Conflict.

    A state conflict occurred (e.g., duplicate resource, invalid state transition).
    """


class RateLimitError(APIError):
    """Raised when the API returns 429 Too Many Requests.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the API.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 429,
        detail: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, status_code=status_code, detail=detail)


class ServerError(APIError):
    """Raised when the API returns 500, 502, or 503.

    An unexpected server-side error occurred.
    """


# ---------------------------------------------------------------------------
# Status-code → Exception mapping
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[int, type[APIError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: AuthorizationError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def _parse_error_detail(response_json: dict | None) -> str | None:
    """Extract the human-readable error detail from a JSON error response.

    The platform API returns errors in the format ``{"detail": "..."}``
    for REST endpoints, and ``{"error": {"message": "..."}}`` for the
    AG-UI middleware.
    """
    if response_json is None:
        return None

    # Standard REST format: {"detail": "..."}
    detail = response_json.get('detail')
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        # Nested detail: {"detail": {"detail": "...", ...}}
        return detail.get('detail')

    # AG-UI format: {"error": {"message": "..."}}
    error = response_json.get('error')
    if isinstance(error, dict):
        return error.get('message')

    return None


def raise_for_status(response: httpx.Response) -> None:
    """Raise a typed APIError if the response indicates an HTTP error.

    Parses the JSON body (if present) to extract the ``detail`` field
    and maps the HTTP status code to the appropriate exception type.

    Does nothing for 2xx responses.
    """
    if response.is_success:
        return

    # Try to parse error body
    response_json: dict | None = None
    try:
        response_json = response.json()
    except (ValueError, httpx.DecodingError):
        pass

    detail = _parse_error_detail(response_json)
    status_code = response.status_code
    message = detail or f'API request failed with status {status_code}'

    # Special handling for rate limit
    if status_code == 429:
        retry_after: int | None = None
        if response_json and 'retry_after' in response_json:
            try:
                retry_after = int(response_json['retry_after'])
            except (TypeError, ValueError):
                pass
        raise RateLimitError(
            message, status_code=status_code, detail=detail, retry_after=retry_after
        )

    # Look up exception class, fallback to ServerError for 5xx, APIError otherwise
    exc_class = _STATUS_MAP.get(status_code)
    if exc_class is None:
        exc_class = ServerError if status_code >= 500 else APIError

    raise exc_class(message, status_code=status_code, detail=detail)
