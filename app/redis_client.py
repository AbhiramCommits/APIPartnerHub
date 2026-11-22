"""Shared Redis connection helpers."""

import redis

from app.config import get_settings

settings = get_settings()

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return a lazily-created Redis client.

    The client is connection-pooled; a stale connection is retried by redis-py.
    """

    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def set_redis_client(client: redis.Redis | None) -> None:
    """Replace the process-wide client (used by tests and the benchmark harness)."""

    global _client
    _client = client
