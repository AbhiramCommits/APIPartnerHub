"""Machine-readable error envelope and API exception taxonomy."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.context import get_request_id

logger = logging.getLogger("apipartnerhub.errors")
settings = get_settings()


class APIError(Exception):
    """Base class for every partner-facing failure.

    Subclasses define the HTTP status, stable machine type, support code and the section of
    the public error reference partners should read.
    """

    status_code: int = 500
    error_type: str = "internal_error"
    code: str = "INTERNAL-001"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or []
        self.headers = headers or {}
        super().__init__(self.message)

    @property
    def doc_url(self) -> str:
        return f"{settings.DOC_BASE_URL}/errors#{self.error_type}"

    def to_envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "type": self.error_type,
                "message": self.message,
                "code": self.code,
                "doc_url": self.doc_url,
                "request_id": request_id or get_request_id(),
                "details": self.details,
            }
        }

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=self.to_envelope(),
            headers=self.headers,
        )


class AuthError(APIError):
    status_code = 401
    error_type = "authentication_failed"
    code = "AUTH-001"
    default_message = "Missing or invalid credentials."


class ForbiddenScope(APIError):
    status_code = 403
    error_type = "insufficient_scope"
    code = "SCOPE-001"
    default_message = "The credential does not grant the scope required for this operation."


class NotFound(APIError):
    status_code = 404
    error_type = "not_found"
    code = "NF-001"
    default_message = "The requested resource does not exist."


class ValidationFailed(APIError):
    status_code = 422
    error_type = "validation_failed"
    code = "VAL-001"
    default_message = "The request could not be validated."


class ConflictError(APIError):
    status_code = 409
    error_type = "conflict"
    code = "CONFLICT-001"
    default_message = "The request conflicts with the current state of the resource."


class RateLimited(APIError):
    status_code = 429
    error_type = "rate_limited"
    code = "RL-001"
    default_message = "Rate limit exceeded for this credential."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 1,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.retry_after = max(1, int(retry_after))
        merged = {"Retry-After": str(self.retry_after)}
        if headers:
            merged.update(headers)
        super().__init__(message, details=details, headers=merged)


class InternalError(APIError):
    status_code = 500
    error_type = "internal_error"
    code = "INTERNAL-001"
    default_message = "An unexpected error occurred. Contact support with the request id."


def error_response(exc: APIError) -> JSONResponse:
    return exc.to_response()


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    if exc.status_code >= 500:
        logger.exception("api_error", extra={"error_type": exc.error_type})
    return error_response(exc)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details: list[dict[str, Any]] = []
    for item in exc.errors():
        location = item.get("loc", ())
        field = ".".join(str(part) for part in location if part not in ("body", "query", "path"))
        details.append(
            {
                "field": field or "request",
                "message": str(item.get("msg", "invalid value")),
                "value": _safe_value(item.get("input")),
            }
        )
    return ValidationFailed("Request validation failed.", details=details).to_response()


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        mapped: APIError = NotFound("The requested endpoint does not exist.")
    elif exc.status_code == 405:
        mapped = ValidationFailed("Method not allowed for this endpoint.")
    elif exc.status_code == 401:
        mapped = AuthError()
    else:
        mapped = ValidationFailed(str(exc.detail))
    return mapped.to_response()


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception")
    # Keep the exception handler cheap and non-blocking for the event loop.
    await run_in_threadpool(lambda: None)
    return InternalError().to_response()


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = value if not isinstance(value, str) else value[:200]
        return text
    return str(type(value).__name__)
