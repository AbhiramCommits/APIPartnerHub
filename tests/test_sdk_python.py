"""Python SDK against the ASGI app: pagination, retry contract and typed errors."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from apipartnerhub import (
    APIPartnerHubClient,
    AuthenticationError,
    NotFoundError,
    RateLimitedError,
    RetryPolicy,
    ValidationError,
)

from app.main import app as fastapi_app
from app.redis_client import get_redis
from tests.conftest import SeedPartner


def transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=fastapi_app)


async def test_pagination_generator_follows_cursors(make_partner: Any, seed_campaign: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read"])
    ids = [seed_campaign(partner.id, f"SDK Campaign {index}") for index in range(5)]
    async with APIPartnerHubClient(
        "http://testserver", api_key=partner.api_key, transport=transport()
    ) as client:
        collected = [campaign["id"] async for campaign in client.list_campaigns(limit=2)]
    assert len(collected) == 5
    assert set(collected) == set(ids)


async def test_retry_honours_retry_after(
    make_partner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    partner: SeedPartner = make_partner(tier="free", rpm=60, scopes=["partner:read"])
    clock = {"now": time.time()}
    monkeypatch.setattr("app.ratelimit.time.time", lambda: clock["now"])
    bucket = f"ratelimit:{partner.tier}:{partner.id}"
    get_redis().hset(bucket, mapping={"tokens": 0, "ts": int(clock["now"] * 1000)})
    get_redis().expire(bucket, 300)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    async with APIPartnerHubClient(
        "http://testserver",
        api_key=partner.api_key,
        transport=transport(),
        retry_policy=RetryPolicy(max_retries=2),
        sleep_func=fake_sleep,
    ) as client:
        result = await client.get_me()

    assert result["name"] == partner.name
    assert result.attempts == 2
    assert client.last_attempts == 2
    assert sleeps == [1.0]
    assert result.request_id


async def test_typed_not_found_error(make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read"])
    async with APIPartnerHubClient(
        "http://testserver", api_key=partner.api_key, transport=transport()
    ) as client:
        with pytest.raises(NotFoundError) as excinfo:
            await client.get_campaign(999_999)
    error = excinfo.value
    assert error.status_code == 404
    assert error.code == "NF-001"
    assert error.request_id
    assert error.doc_url.endswith("#not_found")


async def test_typed_validation_error(make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read"])
    async with APIPartnerHubClient(
        "http://testserver", api_key=partner.api_key, transport=transport()
    ) as client:
        with pytest.raises(ValidationError) as excinfo:
            await client.request("GET", "/v1/campaigns", params={"limit": 500})
    error = excinfo.value
    assert error.status_code == 422
    assert error.code == "VAL-001"
    assert error.details[0]["field"] == "limit"


async def test_typed_authentication_error() -> None:
    async with APIPartnerHubClient(
        "http://testserver",
        api_key="apph_live_deadbeef_invalid",
        transport=transport(),
    ) as client:
        with pytest.raises(AuthenticationError) as excinfo:
            await client.get_me()
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "AUTH-001"


async def test_oauth_client_fetches_token(make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    async with APIPartnerHubClient(
        "http://testserver",
        client_id=partner.client_id,
        client_secret=partner.client_secret,
        transport=transport(),
    ) as client:
        me = await client.get_me()
    assert me["auth_mode"] == "oauth"
    assert client.last_attempts == 1


async def test_rate_limited_error_carries_retry_after(
    make_partner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    partner: SeedPartner = make_partner(tier="free", rpm=60, scopes=["partner:read"])
    clock = {"now": time.time()}
    monkeypatch.setattr("app.ratelimit.time.time", lambda: clock["now"])
    bucket = f"ratelimit:{partner.tier}:{partner.id}"
    get_redis().hset(bucket, mapping={"tokens": 0, "ts": int(clock["now"] * 1000)})

    async def no_sleep(seconds: float) -> None:
        return None

    async with APIPartnerHubClient(
        "http://testserver",
        api_key=partner.api_key,
        transport=transport(),
        retry_policy=RetryPolicy(max_retries=0),
        sleep_func=no_sleep,
    ) as client:
        with pytest.raises(RateLimitedError) as excinfo:
            await client.get_me()
    error = excinfo.value
    assert error.retry_after == pytest.approx(1.0)
    assert error.request_id


async def test_bulk_upload_through_sdk(make_partner: Any, tmp_path: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    csv_file = tmp_path / "campaigns.csv"
    csv_file.write_text(
        "name,status,daily_budget_cents,objective\nSDK Bulk,active,1000,awareness\n",
        encoding="utf-8",
    )
    async with APIPartnerHubClient(
        "http://testserver", api_key=partner.api_key, transport=transport()
    ) as client:
        result = await client.bulk_upload_campaigns(csv_file)
        template = await client.download_bulk_template()
    assert result["accepted"] == 1
    assert result["rejected"] == 0
    assert template.startswith("name,status,daily_budget_cents,objective")
