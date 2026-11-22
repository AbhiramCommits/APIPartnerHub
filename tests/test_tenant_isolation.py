"""Tenant isolation: no endpoint leaks another partner's rows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tests.conftest import SeedPartner


async def test_cannot_read_another_partners_campaign(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    owner: SeedPartner = make_partner("Owner")
    intruder: SeedPartner = make_partner("Intruder")
    campaign_id = seed_campaign(owner.id, "Private Campaign")

    response = await client.get(f"/v1/campaigns/{campaign_id}", headers=intruder.headers)
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["type"] == "not_found"
    assert error["code"] == "NF-001"
    assert str(campaign_id) in error["message"]


async def test_cannot_patch_another_partners_campaign(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    owner: SeedPartner = make_partner("Owner")
    intruder: SeedPartner = make_partner("Intruder")
    campaign_id = seed_campaign(owner.id, "Private Campaign")
    response = await client.patch(
        f"/v1/campaigns/{campaign_id}",
        headers=intruder.headers,
        json={"status": "ended"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


async def test_list_contains_only_own_rows(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    owner: SeedPartner = make_partner("Owner")
    intruder: SeedPartner = make_partner("Intruder")
    owner_campaign = seed_campaign(owner.id, "Owner Campaign")
    seed_campaign(intruder.id, "Intruder Campaign")

    response = await client.get("/v1/campaigns", headers=intruder.headers)
    ids = [row["id"] for row in response.json()["data"]]
    assert ids != [owner_campaign]
    assert all(row["name"] != "Owner Campaign" for row in response.json()["data"])


async def test_metrics_are_tenant_scoped(
    client: Any,
    make_partner: Any,
    seed_campaign: Any,
    seed_metric: Any,
) -> None:
    owner: SeedPartner = make_partner("Owner")
    intruder: SeedPartner = make_partner("Intruder")
    campaign_id = seed_campaign(owner.id, "Owner Campaign")
    seed_metric(
        campaign_id,
        date.today() - timedelta(days=1),
        impressions=1000,
        clicks=50,
        spend_cents=5000,
        conversions=5,
    )

    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": (date.today() - timedelta(days=7)).isoformat(),
            "end_date": date.today().isoformat(),
            "group_by": "campaign",
            "campaign_ids": [campaign_id],
        },
        headers=intruder.headers,
    )
    assert response.status_code == 200
    assert response.json()["row_count"] == 0
    assert response.json()["data"] == []


async def test_same_campaign_query_returns_own_copy(
    client: Any, make_partner: Any, seed_campaign: Any
) -> None:
    owner: SeedPartner = make_partner("Owner")
    intruder: SeedPartner = make_partner("Intruder")
    owner_campaign = seed_campaign(owner.id, "Owner Campaign")
    intruder_campaign = seed_campaign(intruder.id, "Intruder Campaign")

    owner_view = await client.get(f"/v1/campaigns/{owner_campaign}", headers=owner.headers)
    intruder_view = await client.get(f"/v1/campaigns/{intruder_campaign}", headers=intruder.headers)
    assert owner_view.json()["name"] == "Owner Campaign"
    assert intruder_view.json()["name"] == "Intruder Campaign"
