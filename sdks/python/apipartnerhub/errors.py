"""Typed SDK exceptions mapped 1:1 from the API error envelope."""

from __future__ import annotations

from typing import Any


class APIPartnerHubError(Exception):
    """Base error carrying the envelope fields needed for support escalation."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        code: str | None = None,
        doc_url: str | None = None,
        request_id: str | None = None,
        details: list[dict[str, Any]] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.doc_url = doc_url
        self.request_id = request_id
        self.details = details or []
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [self.message]
        if self.code:
            parts.append(f"[{self.code}]")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " ".join(parts)

    @classmethod
    def from_envelope(
        cls,
        status_code: int,
        payload: dict[str, Any],
        *,
        retry_after: float | None = None,
        request_id_header: str | None = None,
    ) -> APIPartnerHubError:
        envelope = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(envelope, str):
            rfc_message = payload.get("error_description") or envelope
            return APIPartnerHubError(
                f"{envelope}: {rfc_message}",
                status_code=status_code,
                error_type=envelope,
                code=f"OAUTH-{status_code}",
                details=[],
                request_id=request_id_header,
                retry_after=retry_after,
            )
        error_class = _class_for_status(status_code)
        if not isinstance(envelope, dict):
            return error_class(
                f"HTTP {status_code} response did not contain the API error envelope.",
                status_code=status_code,
                error_type="http_error",
                code=f"HTTP-{status_code}",
                details=[{"field": "response", "message": "non-envelope error body"}],
                request_id=request_id_header,
                retry_after=retry_after,
            )
        return error_class(
            str(envelope.get("message", "API request failed")),
            status_code=status_code,
            error_type=envelope.get("type"),
            code=envelope.get("code"),
            doc_url=envelope.get("doc_url"),
            request_id=envelope.get("request_id") or request_id_header,
            details=envelope.get("details") or [],
            retry_after=retry_after,
        )


class AuthenticationError(APIPartnerHubError):
    """401 AUTH-001."""


class ForbiddenError(APIPartnerHubError):
    """403 SCOPE-001."""


class NotFoundError(APIPartnerHubError):
    """404 NF-001."""


class ConflictError(APIPartnerHubError):
    """409 CONFLICT-001."""


class ValidationError(APIPartnerHubError):
    """422 VAL-001. ``details`` contains row-level CSV errors when applicable."""


class RateLimitedError(APIPartnerHubError):
    """429 RL-001. ``retry_after`` is the server-mandated delay in seconds."""


class ServerError(APIPartnerHubError):
    """5xx INTERNAL-001 or an upstream failure."""


class APIConnectionError(APIPartnerHubError):
    """The request could not reach the API after exhausting retries."""

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        super().__init__(message, status_code=None, error_type="connection_error")
        self.attempts = attempts


def _class_for_status(status_code: int) -> type[APIPartnerHubError]:
    mapping: dict[int, type[APIPartnerHubError]] = {
        401: AuthenticationError,
        403: ForbiddenError,
        404: NotFoundError,
        409: ConflictError,
        422: ValidationError,
        429: RateLimitedError,
    }
    if status_code in mapping:
        return mapping[status_code]
    if status_code >= 500:
        return ServerError
    return APIPartnerHubError
