"""FastAPI application entrypoint for the APIPartnerHub partner API."""

from __future__ import annotations

import logging
from typing import Any

import redis
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
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
from app.redis_client import get_redis
from app.routers import bulk, campaigns, oauth, partners, reports

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

    app.include_router(oauth.router)
    app.include_router(partners.router, prefix="/v1")
    app.include_router(campaigns.router, prefix="/v1")
    app.include_router(reports.router, prefix="/v1")
    app.include_router(bulk.router, prefix="/v1")

    @app.get(
        "/healthz",
        tags=["Health"],
        summary="Health check",
        description="Pings Postgres and Redis. Returns 503 when a dependency is down.",
    )
    async def healthz(
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

    return app


app = create_app()
