"""Rate limiting: enforcement, headers, Retry-After and Redis fail-open."""

from __future__ import annotations

import logging
from typing import Any

import redis

from app.redis_client import set_redis_client
from tests.conftest import SeedPartner


class BrokenRedis:
    """Minimal stand-in that makes every rate-limit call fail."""

    def register_script(self, script: str) -> Any:
        raise redis.RedisError("simulated redis outage")


async def test_limit_is_enforced_with_headers(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(tier="free", rpm=5, scopes=["partner:read"])
    statuses: list[int] = []
    allowed_headers: list[Any] = []
    limited_bodies: list[dict[str, Any]] = []

    for _ in range(12):
        response = await client.get("/v1/partners/me", headers=partner.headers)
        statuses.append(response.status_code)
        if response.status_code == 200:
            allowed_headers.append(response.headers)
        elif response.status_code == 429:
            limited_bodies.append(response.json())

    assert statuses.count(200) == 10
    assert statuses.count(429) == 2
    for headers in allowed_headers:
        assert headers["RateLimit-Limit"] == "10"
        assert int(headers["RateLimit-Remaining"]) >= 0
        assert int(headers["RateLimit-Reset"]) >= 0

    for body in limited_bodies:
        error = body["error"]
        assert error["type"] == "rate_limited"
        assert error["code"] == "RL-001"
        assert error["details"][0]["field"] == "rate_limit"


async def test_retry_after_is_sane(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(tier="free", rpm=5, scopes=["partner:read"])
    for _ in range(10):
        assert (await client.get("/v1/partners/me", headers=partner.headers)).status_code == 200
    response = await client.get("/v1/partners/me", headers=partner.headers)
    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert retry_after >= 1
    assert response.headers["RateLimit-Remaining"] == "0"
    assert response.headers["RateLimit-Limit"] == "10"


async def test_unauthorized_responses_still_carry_rate_limit_headers(client: Any) -> None:
    response = await client.get("/v1/partners/me", headers={"X-API-Key": "bad"})
    assert response.status_code == 401
    assert response.headers["RateLimit-Limit"]
    assert response.headers["RateLimit-Remaining"]
    assert response.headers["X-Request-ID"]


async def test_fail_open_when_redis_is_down(client: Any, make_partner: Any, caplog: Any) -> None:
    partner: SeedPartner = make_partner(tier="free", rpm=5, scopes=["partner:read"])
    set_redis_client(BrokenRedis())  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING)
    for _ in range(15):
        response = await client.get("/v1/partners/me", headers=partner.headers)
        assert response.status_code == 200
        assert response.headers["RateLimit-Limit"] == "10"
        assert response.headers["RateLimit-Remaining"] == "10"
    assert any(
        "rate_limiter_redis_unavailable_fail_open" in record.message for record in caplog.records
    )
