"""Error envelope taxonomy and request-id plumbing."""

from __future__ import annotations

from typing import Any

import pytest

from app.errors import (
    APIError,
    AuthError,
    ConflictError,
    ForbiddenScope,
    InternalError,
    NotFound,
    RateLimited,
    ValidationFailed,
)

ERROR_CLASSES = [
    (AuthError, 401, "authentication_failed", "AUTH-001"),
    (ForbiddenScope, 403, "insufficient_scope", "SCOPE-001"),
    (NotFound, 404, "not_found", "NF-001"),
    (ConflictError, 409, "conflict", "CONFLICT-001"),
    (ValidationFailed, 422, "validation_failed", "VAL-001"),
    (RateLimited, 429, "rate_limited", "RL-001"),
    (InternalError, 500, "internal_error", "INTERNAL-001"),
]


@pytest.mark.parametrize("error_class,status,error_type,code", ERROR_CLASSES)
async def test_every_error_serializes_to_the_envelope(
    error_class: type[APIError], status: int, error_type: str, code: str
) -> None:
    error = error_class("boom", details=[{"field": "x", "message": "bad"}])
    envelope = error.to_envelope("request-123")
    assert error.status_code == status
    assert envelope["error"]["type"] == error_type
    assert envelope["error"]["code"] == code
    assert envelope["error"]["message"] == "boom"
    assert envelope["error"]["request_id"] == "request-123"
    assert envelope["error"]["doc_url"] == (f"https://docs.apipartnerhub.dev/errors#{error_type}")
    assert envelope["error"]["details"] == [{"field": "x", "message": "bad"}]


async def test_rate_limited_retry_after() -> None:
    error = RateLimited(retry_after=7)
    response = error.to_response("rid")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


async def test_unknown_endpoint_uses_envelope(client: Any) -> None:
    response = await client.get("/v1/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NF-001"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_request_id_is_propagated(client: Any) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-abc-123"


async def test_invalid_request_id_is_replaced(client: Any) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "short"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "short"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_validation_errors_are_enveloped(client: Any, make_partner: Any) -> None:
    partner = make_partner(scopes=["campaigns:read"])
    response = await client.get("/v1/campaigns", params={"limit": 999}, headers=partner.headers)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "validation_failed"
    assert error["details"][0]["field"] == "limit"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_error_details_default_to_empty() -> None:
    envelope = NotFound().to_envelope("rid")
    assert envelope["error"]["details"] == []
    assert envelope["error"]["message"] == "The requested resource does not exist."
