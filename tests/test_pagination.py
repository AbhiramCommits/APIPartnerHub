"""Cursor pagination: complete, duplicate-free, stable walks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.pagination import decode_cursor, encode_cursor
from tests.conftest import SeedPartner


async def test_cursor_walks_entire_set_once(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    partner: SeedPartner = make_partner()
    now = datetime.now(UTC)
    ids = [
        seed_campaign(
            partner.id, f"Campaign {index:02d}", created_at=now - timedelta(minutes=index)
        )
        for index in range(25)
    ]

    seen: list[int] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/v1/campaigns", params=params, headers=partner.headers)
        assert response.status_code == 200
        payload = response.json()
        seen.extend(row["id"] for row in payload["data"])
        pages += 1
        if not payload["has_more"]:
            assert payload["next_cursor"] is None
            break
        assert payload["next_cursor"]
        cursor = payload["next_cursor"]

    assert pages == 4
    assert len(seen) == 25
    assert len(set(seen)) == 25
    assert set(seen) == set(ids)
    assert seen == sorted(ids)


async def test_ordering_is_stable_across_walks(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    partner: SeedPartner = make_partner()
    now = datetime.now(UTC)
    for index in range(10):
        seed_campaign(partner.id, f"Stable {index}", created_at=now - timedelta(seconds=index))

    async def walk() -> list[int]:
        collected: list[int] = []
        cursor = None
        while True:
            params = {"limit": 3}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/v1/campaigns", params=params, headers=partner.headers)
            payload = response.json()
            collected.extend(row["id"] for row in payload["data"])
            if not payload["has_more"]:
                return collected
            cursor = payload["next_cursor"]

    assert await walk() == await walk()


async def test_cursor_round_trip() -> None:
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    decoded_time, decoded_id = decode_cursor(encode_cursor(created, 42))
    assert decoded_time == created
    assert decoded_id == 42


async def test_bad_cursor(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    response = await client.get(
        "/v1/campaigns", params={"cursor": "not-a-cursor"}, headers=partner.headers
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "validation_failed"
    assert error["details"][0]["field"] == "cursor"


async def test_limit_bounds(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    too_large = await client.get("/v1/campaigns", params={"limit": 101}, headers=partner.headers)
    assert too_large.status_code == 422
    assert too_large.json()["error"]["details"][0]["field"] == "limit"

    zero = await client.get("/v1/campaigns", params={"limit": 0}, headers=partner.headers)
    assert zero.status_code == 422


async def test_page_size_one_has_no_duplicates(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    partner: SeedPartner = make_partner()
    now = datetime.now(UTC)
    for index in range(5):
        seed_campaign(partner.id, f"Single {index}", created_at=now - timedelta(seconds=index))
    seen: list[int] = []
    cursor = None
    while True:
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        payload = (await client.get("/v1/campaigns", params=params, headers=partner.headers)).json()
        seen.extend(row["id"] for row in payload["data"])
        if not payload["has_more"]:
            break
        cursor = payload["next_cursor"]
    assert len(seen) == 5
    assert len(set(seen)) == 5
