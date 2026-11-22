"""FastAPI application entrypoint for the APIPartnerHub partner API."""

from __future__ import annotations

import logging
from typing import Any

import redis
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import get_settings
from app.db import get_session
from app.errors import (
    APIError,
    api_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.logging_config import configure_logging
from app.middleware import AccessLogMiddleware, RateLimitMiddleware, RequestContextMiddleware
from app.redis_client import get_redis
from app.routers import bulk, campaigns, oauth, ops, partners, reports

configure_logging()

settings = get_settings()
logger = logging.getLogger("apipartnerhub")

TAGS_METADATA = [
    {"name": "Health", "description": "Liveness and dependency checks."},
    {
        "name": "OAuth",
        "description": "OAuth 2.0 client-credentials token issuance for integration partners.",
    },
    {"name": "Partners", "description": "The authenticated partner profile and entitlements."},
    {"name": "Campaigns", "description": "Campaign CRUD with cursor pagination and idempotency."},
    {"name": "Reports", "description": "Aggregated performance metrics as JSON or streamed CSV."},
    {"name": "Bulk", "description": "CSV campaign import and downloadable templates."},
    {"name": "Operations", "description": "In-process observability for support engineering."},
]

DESCRIPTION = """
Partner-facing advertising data API.

**Authentication** — send either a long-lived `X-API-Key` credential or an OAuth 2.0
client-credentials bearer token (`Authorization: Bearer <jwt>`). Scopes are enforced per
endpoint. Mint tokens with `POST /oauth/token`.

**Reliability** — every `/v1` response carries `RateLimit-Limit`, `RateLimit-Remaining` and
`RateLimit-Reset`, plus an `X-Request-ID`. Failures use one machine-readable envelope with a
stable `code`, a `doc_url` and the request id for support escalation.

**Bulk data** — campaigns can be created in bulk from CSV and reports can be streamed as CSV
for spreadsheet workflows.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="APIPartnerHub Partner API",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS_METADATA,
        contact={"name": "Partner Solutions Engineering", "email": "partners@apipartnerhub.dev"},
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        servers=[
            {"url": settings.API_BASE_URL, "description": "Local development"},
            {"url": "https://api.apipartnerhub.dev", "description": "Production"},
        ],
    )

    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Added innermost first: request context must be the outermost layer so every log line
    # and error envelope (including rate-limit rejections) carries the request id.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(oauth.router)
    app.include_router(partners.router, prefix="/v1")
    app.include_router(campaigns.router, prefix="/v1")
    app.include_router(reports.router, prefix="/v1")
    app.include_router(bulk.router, prefix="/v1")
    app.include_router(ops.router, prefix="/v1")

    @app.get(
        "/healthz",
        tags=["Health"],
        summary="Health check",
        description="Pings Postgres and Redis. Returns 503 when a dependency is down.",
    )
    def healthz(
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        checks: dict[str, Any] = {"db": "ok", "redis": "ok"}
        healthy = True
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            logger.warning("healthz_db_failed", exc_info=True)
            checks["db"] = "error"
            healthy = False
        try:
            get_redis().ping()
        except redis.RedisError:
            logger.warning("healthz_redis_failed", exc_info=True)
            checks["redis"] = "error"
            healthy = False
        body = {
            "status": "ok" if healthy else "degraded",
            "version": __version__,
            "env": settings.ENV,
            **checks,
        }
        return JSONResponse(status_code=200 if healthy else 503, content=body)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=TAGS_METADATA,
            servers=app.servers,
            contact={
                "name": "Partner Solutions Engineering",
                "email": "partners@apipartnerhub.dev",
            },
            license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": (
                "Long-lived partner key formatted as `apph_live_<prefix>_<secret>`. "
                "Send it on every request."
            ),
        }
        security_schemes["OAuth2"] = {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "/oauth/token",
                    "scopes": {
                        "partner:read": "Read the partner profile and internal stats.",
                        "campaigns:read": "Read campaigns and their configuration.",
                        "campaigns:write": "Create and update campaigns.",
                        "reports:read": "Read aggregated performance reports.",
                        "bulk:write": "Import campaigns from CSV.",
                    },
                }
            },
        }
        for name, component in components.get("schemas", {}).items():
            if isinstance(component, dict) and not component.get("description"):
                component["description"] = _schema_description(name)
        for path, operations in schema.get("paths", {}).items():
            for method, operation in operations.items():
                if not isinstance(operation, dict):
                    continue
                if path == "/oauth/token":
                    operation["security"] = []
                    continue
                if path == "/healthz":
                    operation["security"] = []
                    operation.setdefault("responses", {}).setdefault(
                        "503", {"description": "Postgres or Redis is unavailable."}
                    )
                    continue
                if not path.startswith("/v1/"):
                    continue
                scope = _required_scope(path, method)
                operation["security"] = [{"ApiKeyAuth": []}, {"OAuth2": [scope]}]
                responses = operation.setdefault("responses", {})
                responses.setdefault(
                    "401", {"description": "Missing or invalid credentials (AUTH-001)."}
                )
                responses.setdefault(
                    "403", {"description": "Credential lacks the required scope (SCOPE-001)."}
                )
                responses.setdefault(
                    "429",
                    {
                        "description": (
                            "Rate limit exceeded (RL-001). Retry after the Retry-After seconds."
                        )
                    },
                )
                responses.setdefault(
                    "500", {"description": "Unexpected server error (INTERNAL-001)."}
                )
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


def _schema_description(name: str) -> str:
    if name.startswith("Body_issue_token"):
        return (
            "Form-encoded OAuth 2.0 client-credentials request: grant_type, client_id, "
            "client_secret and optional scope."
        )
    if name.startswith("Body_bulk_campaigns"):
        return "Multipart form body containing the CSV file in the `file` field."
    if name in {"HTTPValidationError", "ValidationError"}:
        return "Framework-level validation detail schema (API errors use the error envelope)."
    return f"{name} payload schema."


def _required_scope(path: str, method: str) -> str:
    if path.startswith("/v1/campaigns"):
        return "campaigns:write" if method in {"post", "patch"} else "campaigns:read"
    if path.startswith("/v1/reports"):
        return "reports:read"
    if path.startswith("/v1/bulk"):
        return "bulk:write"
    return "partner:read"


app = create_app()
