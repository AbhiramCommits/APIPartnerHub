"""In-process observability counters and the stats endpoint."""

from __future__ import annotations

from typing import Any

from tests.conftest import SeedPartner


async def test_stats_reflect_requests(client: Any, make_partner: Any, seed_campaign: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read", "campaigns:read"])
    campaign_id = seed_campaign(partner.id, "Stats Campaign")

    for _ in range(3):
        assert (await client.get("/v1/partners/me", headers=partner.headers)).status_code == 200
    assert (
        await client.get(f"/v1/campaigns/{campaign_id}", headers=partner.headers)
    ).status_code == 200

    first = await client.get("/v1/_internal/stats", headers=partner.headers)
    assert first.status_code == 200
    stats = first.json()
    assert stats["requests_total"] >= 4
    assert stats["requests_by_status_class"]["2xx"] >= 4
    assert stats["rate_limited_total"] == 0
    assert stats["requests_by_auth_mode"]["api_key"] >= 4
    assert stats["latency_ms"]["samples"] >= 4
    assert stats["latency_ms"]["p95"] >= stats["latency_ms"]["p50"]
    assert "GET /v1/partners/me" in stats["endpoints"]
    assert "GET /v1/campaigns/{campaign_id}" in stats["endpoints"]
    assert stats["endpoints"]["GET /v1/partners/me"]["count"] >= 3
    assert stats["uptime_seconds"] >= 0

    second = await client.get("/v1/_internal/stats", headers=partner.headers)
    assert "GET /v1/_internal/stats" in second.json()["endpoints"]


async def test_rate_limited_counter(client: Any, make_partner: Any) -> None:
    limited_partner: SeedPartner = make_partner(tier="free", rpm=2, scopes=["partner:read"])
    ops_partner: SeedPartner = make_partner("Ops", scopes=["partner:read"])
    statuses = [
        (await client.get("/v1/partners/me", headers=limited_partner.headers)).status_code
        for _ in range(6)
    ]
    assert 429 in statuses
    stats = (await client.get("/v1/_internal/stats", headers=ops_partner.headers)).json()
    assert stats["rate_limited_total"] >= 1
    assert stats["requests_by_status_class"]["4xx"] >= 1


async def test_observability_snapshot_and_reset() -> None:
    from app import observability

    observability.reset()
    empty = observability.snapshot()
    assert empty["requests_total"] == 0
    assert empty["latency_ms"]["p95"] == 0.0
    observability.record_request(
        method="get", path_template="/x", status_code=200, latency_ms=10.0, auth_mode="api_key"
    )
    observability.record_request(
        method="GET", path_template="/x", status_code=429, latency_ms=30.0, auth_mode=None
    )
    snapshot = observability.snapshot()
    assert snapshot["requests_total"] == 2
    assert snapshot["endpoints"]["GET /x"]["count"] == 2
    assert snapshot["latency_ms"]["max"] == 30.0
    assert snapshot["requests_by_auth_mode"]["none"] == 1
    observability.reset()
    assert observability.snapshot()["requests_total"] == 0
