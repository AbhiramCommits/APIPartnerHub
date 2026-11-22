"""JSON logging configuration for API and structured access logs."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.context import get_request_id

_EXTRA_FIELDS = (
    "request_id",
    "partner_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "rate_limit_remaining",
    "auth_mode",
    "error_type",
    "jti",
    "client_id",
    "endpoint",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class AccessFormatter(logging.Formatter):
    """Access middleware already emits a JSON object; do not double-wrap it."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    if not any(getattr(handler, "_apipartnerhub", False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler._apipartnerhub = True  # type: ignore[attr-defined]
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]

    access = logging.getLogger("apipartnerhub.access")
    access.setLevel(level)
    access.propagate = False
    if not any(getattr(handler, "_apipartnerhub_access", False) for handler in access.handlers):
        access_handler = logging.StreamHandler(sys.stdout)
        access_handler._apipartnerhub_access = True  # type: ignore[attr-defined]
        access_handler.setFormatter(AccessFormatter())
        access.handlers = [access_handler]

    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)
