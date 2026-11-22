"""ASGI middleware: request context, rate limiting and structured access logging."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app import observability
from app.auth import authenticate
from app.config import get_settings
from app.context import request_id_ctx
from app.db import SessionLocal
from app.errors import APIError, RateLimited
from app.ratelimit import RateLimitDecision, limiter

logger = logging.getLogger("apipartnerhub.middleware")
access_logger = logging.getLogger("apipartnerhub.access")
settings = get_settings()

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Generate or propagate X-Request-ID and bind it to a contextvar."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the Redis token bucket to every authenticated /v1/* request.

    Authentication happens here first (and is cached on request.state for the route
    dependency), so limits are always partner-specific. Redis failures fail open: a partner
    never receives a 500 because the rate limiter is unavailable.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/v1/") or request.method == "OPTIONS":
            return await call_next(request)

        default_limit = 2 * settings.RATE_LIMIT_DEFAULT_RPM
        decision = RateLimitDecision(
            allowed=True,
            limit=default_limit,
            remaining=default_limit,
            reset_after=60,
            retry_after=0,
        )
        try:
            with SessionLocal() as session:
                context = authenticate(request, session)
            decision = limiter.check(
                context.partner.id,
                context.partner.tier,
                context.partner.rate_limit_rpm,
            )
        except APIError:
            # The route dependency raises the same authored error with the full envelope.
            pass
        except Exception:  # pragma: no cover - defensive fail-open path
            logger.warning("rate_limit_identity_failed_fail_open", exc_info=True)

        request.state.rate_limit_remaining = decision.remaining
        if not decision.allowed:
            error = RateLimited(
                "Rate limit exceeded. Retry after the number of seconds in Retry-After.",
                retry_after=decision.retry_after,
                details=[
                    {
                        "field": "rate_limit",
                        "message": (
                            f"Limit {decision.limit} requests per burst window; "
                            "retry after the indicated delay."
                        ),
                    }
                ],
                headers=decision.headers(),
            )
            return error.to_response()

        response = await call_next(request)
        for header, value in decision.headers().items():
            response.headers[header] = value
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one JSON line per request and feed the in-process observability counters."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            latency_ms = (time.perf_counter() - started) * 1000
            _log_access(request, status_code, latency_ms)
            raise
        latency_ms = (time.perf_counter() - started) * 1000
        _log_access(request, status_code, latency_ms)
        return response


def _path_template(request: Request) -> str:
    """Return the matched route template, including the router prefix.

    FastAPI's lazily-included routers expose the unprefixed route path on the matched
    route, so align it with the actual request path.
    """

    route = request.scope.get("route")
    route_path_raw = getattr(route, "path", None)
    actual = request.url.path
    if not isinstance(route_path_raw, str) or not route_path_raw:
        return actual
    route_path = route_path_raw
    if route_path == actual:
        return actual
    for segment in route_path.strip("/").split("/"):
        if segment and "{" not in segment:
            marker = f"/{segment}"
            index = actual.find(marker)
            if index >= 0:
                return actual[:index] + route_path
    return re.sub(r"/\d+(?=/|$)", "/{id}", actual)


def _log_access(request: Request, status_code: int, latency_ms: float) -> None:
    path_template = _path_template(request)
    state = request.state
    partner_id = getattr(state, "partner_id", None)
    auth_mode = getattr(state, "auth_mode", None)
    remaining = getattr(state, "rate_limit_remaining", None)
    request_id = getattr(state, "request_id", None)

    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        + f".{int(time.time() * 1000) % 1000:03d}Z",
        "request_id": request_id,
        "partner_id": partner_id,
        "method": request.method,
        "path": request.url.path,
        "status": status_code,
        "latency_ms": round(latency_ms, 2),
        "rate_limit_remaining": remaining,
        "auth_mode": auth_mode,
    }
    access_logger.info(json.dumps(payload, separators=(",", ":"), default=str))
    observability.record_request(
        method=request.method,
        path_template=path_template,
        status_code=status_code,
        latency_ms=latency_ms,
        auth_mode=auth_mode,
    )
