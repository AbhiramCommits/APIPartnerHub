"""Health checks including dependency failure paths."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import redis
from sqlalchemy import inspect

from app.db import engine, get_session, init_db
from app.main import app as fastapi_app
from app.redis_client import set_redis_client


class NoPingRedis:
    def ping(self) -> bool:
        raise redis.RedisError("redis down")


class BrokenSession:
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db down")


async def broken_session() -> AsyncIterator[BrokenSession]:
    yield BrokenSession()


async def test_healthz_ok(client: Any) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"
    assert body["version"] == "1.0.0"


async def test_healthz_redis_down(client: Any) -> None:
    set_redis_client(NoPingRedis())  # type: ignore[arg-type]
    response = await client.get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "ok"
    assert body["redis"] == "error"


async def test_healthz_db_down(client: Any) -> None:
    fastapi_app.dependency_overrides[get_session] = broken_session
    try:
        response = await client.get("/healthz")
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "error"
    assert body["redis"] == "ok"


async def test_init_db_creates_tables() -> None:
    init_db()
    tables = set(inspect(engine).get_table_names())
    assert {
        "partners",
        "api_keys",
        "oauth_clients",
        "campaigns",
        "metrics_daily",
        "idempotency_records",
    } <= tables
