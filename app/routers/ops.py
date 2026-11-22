"""Internal observability endpoints for partner support engineering."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app import observability
from app.auth import AuthContext, require_scopes

router = APIRouter(tags=["Operations"])


@router.get(
    "/_internal/stats",
    summary="In-process request statistics",
    description=(
        "Returns counters for the worker process that served this request: request totals, "
        "status-class breakdown, 429 count, auth-mode usage, overall latency percentiles and "
        "per-endpoint latency. With multiple workers the counters are per process."
    ),
)
async def stats(
    context: Annotated[AuthContext, Depends(require_scopes("partner:read"))],
) -> dict[str, Any]:
    return observability.snapshot()
