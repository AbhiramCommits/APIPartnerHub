"""Request-scoped context variables (request id)."""

from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request id, or a stable fallback outside a request."""

    return request_id_ctx.get() or "unknown"
