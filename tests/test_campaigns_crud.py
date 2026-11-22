"""Campaign CRUD and ETag-guarded updates."""

from __future__ import annotations

from typing import Any

from tests.conftest import SeedPartner


async def test_create_get_list_patch(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    created = await client.post(
        "/v1/campaigns",
        headers=partner.headers,
        json={
            "name": "Spring Launch",
            "status": "active",
            "daily_budget_cents": 7500,
            "objective": "conversions",
        },
    )
    assert created.status_code == 201
    campaign = created.json()
    assert campaign["name"] == "Spring Launch"
    assert campaign["daily_budget_cents"] == 7500
    assert created.headers["Location"] == f"/v1/campaigns/{campaign['id']}"
    etag = created.headers["ETag"]
    assert etag.startswith('"') and etag.endswith('"')

    fetched = await client.get(f"/v1/campaigns/{campaign['id']}", headers=partner.headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == campaign["id"]
    assert fetched.headers["ETag"] == etag

    listing = await client.get("/v1/campaigns", headers=partner.headers)
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["has_more"] is False
    assert payload["next_cursor"] is None
    assert [row["id"] for row in payload["data"]] == [campaign["id"]]

    patched = await client.patch(
        f"/v1/campaigns/{campaign['id']}",
        headers={**partner.headers, "If-Match": etag},
        json={"status": "paused", "daily_budget_cents": 9000},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"
    assert patched.json()["daily_budget_cents"] == 9000
    assert patched.json()["name"] == "Spring Launch"
    new_etag = patched.headers["ETag"]
    assert new_etag != etag


async def test_etag_conflict(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    created = await client.post(
        "/v1/campaigns", headers=partner.headers, json={"name": "ETag campaign"}
    )
    campaign_id = created.json()["id"]
    old_etag = created.headers["ETag"]

    first = await client.patch(
        f"/v1/campaigns/{campaign_id}",
        headers={**partner.headers, "If-Match": old_etag},
        json={"status": "paused"},
    )
    assert first.status_code == 200

    stale = await client.patch(
        f"/v1/campaigns/{campaign_id}",
        headers={**partner.headers, "If-Match": old_etag},
        json={"status": "active"},
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["type"] == "conflict"
    assert error["details"][0]["field"] == "If-Match"


async def test_wildcard_if_match(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    created = await client.post("/v1/campaigns", headers=partner.headers, json={"name": "Wildcard"})
    response = await client.patch(
        f"/v1/campaigns/{created.json()['id']}",
        headers={**partner.headers, "If-Match": "*"},
        json={"status": "ended"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ended"


async def test_patch_requires_a_field(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    created = await client.post(
        "/v1/campaigns", headers=partner.headers, json={"name": "Empty patch"}
    )
    response = await client.patch(
        f"/v1/campaigns/{created.json()['id']}", headers=partner.headers, json={}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VAL-001"


async def test_patch_rejects_null(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    created = await client.post(
        "/v1/campaigns", headers=partner.headers, json={"name": "Null patch"}
    )
    response = await client.patch(
        f"/v1/campaigns/{created.json()['id']}",
        headers=partner.headers,
        json={"status": None},
    )
    assert response.status_code == 422
    assert "cannot be null" in response.json()["error"]["message"]


async def test_list_filters(client: Any, make_partner: Any, seed_campaign: Any) -> None:
    partner: SeedPartner = make_partner()
    seed_campaign(partner.id, "Alpha Conversions", status="active", objective="conversions")
    seed_campaign(partner.id, "Beta Awareness", status="paused", objective="awareness")
    seed_campaign(partner.id, "Gamma Conversions", status="active", objective="conversions")

    active = await client.get("/v1/campaigns", params={"status": "active"}, headers=partner.headers)
    assert {row["name"] for row in active.json()["data"]} == {
        "Alpha Conversions",
        "Gamma Conversions",
    }

    conversions = await client.get(
        "/v1/campaigns", params={"objective": "conversions"}, headers=partner.headers
    )
    assert len(conversions.json()["data"]) == 2

    contains = await client.get(
        "/v1/campaigns", params={"name_contains": "beta"}, headers=partner.headers
    )
    assert [row["name"] for row in contains.json()["data"]] == ["Beta Awareness"]


async def test_create_validation(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    response = await client.post(
        "/v1/campaigns",
        headers=partner.headers,
        json={"name": "", "daily_budget_cents": -1, "status": "launched"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "validation_failed"
    fields = {detail["field"] for detail in error["details"]}
    assert {"name", "daily_budget_cents", "status"} <= fields
