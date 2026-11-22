"""Idempotency-Key replay semantics."""

from __future__ import annotations

from typing import Any

from app.db import SessionLocal
from app.models import Campaign
from tests.conftest import SeedPartner


async def test_replay_returns_identical_body(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    headers = {**partner.headers, "Idempotency-Key": "create-001"}
    body = {
        "name": "Idempotent Campaign",
        "status": "active",
        "daily_budget_cents": 1234,
        "objective": "awareness",
    }
    first = await client.post("/v1/campaigns", headers=headers, json=body)
    assert first.status_code == 201
    assert "Idempotency-Replayed" not in first.headers

    second = await client.post("/v1/campaigns", headers=headers, json=body)
    assert second.status_code == 201
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json() == first.json()

    with SessionLocal() as session:
        count = session.query(Campaign).filter(Campaign.partner_id == partner.id).count()
        assert count == 1


async def test_different_body_conflicts(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    headers = {**partner.headers, "Idempotency-Key": "create-002"}
    await client.post(
        "/v1/campaigns", headers=headers, json={"name": "Original", "daily_budget_cents": 10}
    )
    response = await client.post(
        "/v1/campaigns", headers=headers, json={"name": "Different", "daily_budget_cents": 20}
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["type"] == "conflict"
    assert error["code"] == "CONFLICT-001"
    assert error["details"][0]["field"] == "Idempotency-Key"


async def test_key_is_scoped_per_partner(client: Any, make_partner: Any) -> None:
    partner_a: SeedPartner = make_partner("A")
    partner_b: SeedPartner = make_partner("B")
    body = {"name": "Shared key", "daily_budget_cents": 100}
    first = await client.post(
        "/v1/campaigns",
        headers={**partner_a.headers, "Idempotency-Key": "shared-key"},
        json=body,
    )
    second = await client.post(
        "/v1/campaigns",
        headers={**partner_b.headers, "Idempotency-Key": "shared-key"},
        json=body,
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


async def test_without_key_creates_duplicates(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    body = {"name": "No key", "daily_budget_cents": 100}
    first = await client.post("/v1/campaigns", headers=partner.headers, json=body)
    second = await client.post("/v1/campaigns", headers=partner.headers, json=body)
    assert first.json()["id"] != second.json()["id"]


async def test_key_length_is_validated(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    response = await client.post(
        "/v1/campaigns",
        headers={**partner.headers, "Idempotency-Key": "x" * 256},
        json={"name": "Too long"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "Idempotency-Key"
