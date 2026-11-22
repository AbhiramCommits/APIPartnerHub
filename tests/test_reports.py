"""Reports: server-side metric math, grouping, CSV and tenant isolation."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.conftest import SeedPartner

D1 = date(2026, 1, 1)
D2 = date(2026, 1, 2)


@pytest.fixture
def report_data(
    make_partner: Any, seed_campaign: Any, seed_metric: Any
) -> tuple[SeedPartner, int, int]:
    partner: SeedPartner = make_partner(scopes=["reports:read"])
    campaign_a = seed_campaign(partner.id, "Campaign A", objective="conversions")
    campaign_b = seed_campaign(partner.id, "Campaign B", objective="awareness")
    seed_metric(
        campaign_a,
        D1,
        impressions=1000,
        clicks=50,
        spend_cents=10000,
        conversions=5,
    )
    seed_metric(
        campaign_a,
        D2,
        impressions=2000,
        clicks=40,
        spend_cents=8000,
        conversions=4,
    )
    seed_metric(
        campaign_b,
        D1,
        impressions=500,
        clicks=10,
        spend_cents=1000,
        conversions=1,
    )
    seed_metric(
        campaign_b,
        D2,
        impressions=500,
        clicks=5,
        spend_cents=500,
        conversions=0,
    )
    return partner, campaign_a, campaign_b


async def test_group_by_day_math(client: Any, report_data: Any) -> None:
    partner, _, _ = report_data
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "group_by": "day",
        },
        headers=partner.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 2
    first, second = body["data"]

    assert first["date"] == "2026-01-01"
    assert first["impressions"] == 1500
    assert first["clicks"] == 60
    assert first["spend_cents"] == 11000
    assert first["conversions"] == 6
    assert first["ctr_pct"] == pytest.approx(4.0)
    assert first["cpc_cents"] == pytest.approx(183.3333)
    assert first["cpm_cents"] == pytest.approx(7333.3333)
    assert first["cvr_pct"] == pytest.approx(10.0)

    assert second["date"] == "2026-01-02"
    assert second["impressions"] == 2500
    assert second["clicks"] == 45
    assert second["spend_cents"] == 8500
    assert second["conversions"] == 4
    assert second["ctr_pct"] == pytest.approx(1.8)
    assert second["cpc_cents"] == pytest.approx(188.8889)
    assert second["cpm_cents"] == pytest.approx(3400.0)
    assert second["cvr_pct"] == pytest.approx(8.8889)


async def test_group_by_campaign_math(client: Any, report_data: Any) -> None:
    partner, campaign_a, campaign_b = report_data
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "group_by": "campaign",
        },
        headers=partner.headers,
    )
    assert response.status_code == 200
    rows = {row["campaign_id"]: row for row in response.json()["data"]}
    row_a = rows[campaign_a]
    assert row_a["impressions"] == 3000
    assert row_a["clicks"] == 90
    assert row_a["spend_cents"] == 18000
    assert row_a["conversions"] == 9
    assert row_a["ctr_pct"] == pytest.approx(3.0)
    assert row_a["cpc_cents"] == pytest.approx(200.0)
    assert row_a["cpm_cents"] == pytest.approx(6000.0)
    assert row_a["cvr_pct"] == pytest.approx(10.0)

    row_b = rows[campaign_b]
    assert row_b["impressions"] == 1000
    assert row_b["clicks"] == 15
    assert row_b["ctr_pct"] == pytest.approx(1.5)
    assert row_b["cpc_cents"] == pytest.approx(100.0)
    assert row_b["cpm_cents"] == pytest.approx(1500.0)
    assert row_b["cvr_pct"] == pytest.approx(6.6667)


async def test_metric_selection_excludes_unrequested_raw_fields(
    client: Any, report_data: Any
) -> None:
    partner, _, _ = report_data
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "group_by": "day",
            "metrics": "impressions",
        },
        headers=partner.headers,
    )
    body = response.json()
    assert body["metrics"] == ["impressions"]
    assert "clicks" not in body["data"][0]
    assert body["data"][0]["impressions"] == 1500
    assert "ctr_pct" in body["data"][0]


async def test_campaign_filter_and_window(client: Any, report_data: Any) -> None:
    partner, campaign_a, _ = report_data
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D2.isoformat(),
            "end_date": D2.isoformat(),
            "group_by": "campaign",
            "campaign_ids": [campaign_a],
        },
        headers=partner.headers,
    )
    body = response.json()
    assert body["row_count"] == 1
    assert body["data"][0]["campaign_id"] == campaign_a
    assert body["data"][0]["impressions"] == 2000


async def test_csv_output(client: Any, report_data: Any) -> None:
    partner, _, _ = report_data
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "group_by": "day",
            "format": "csv",
        },
        headers=partner.headers,
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in response.headers["Content-Disposition"]
    assert (
        'filename="metrics_2026-01-01_2026-01-02_by_day.csv"'
        in response.headers["Content-Disposition"]
    )
    lines = response.text.strip().splitlines()
    assert lines[0] == (
        "date,impressions,clicks,spend_cents,conversions,ctr_pct,cpc_cents,cpm_cents,cvr_pct"
    )
    assert lines[1].startswith("2026-01-01,1500,60,11000,6,4.0,")
    assert len(lines) == 3


async def test_zero_volume_rows_produce_zero_ratios(
    client: Any, make_partner: Any, seed_campaign: Any, seed_metric: Any
) -> None:
    partner: SeedPartner = make_partner(scopes=["reports:read"])
    campaign_id = seed_campaign(partner.id, "Zero Campaign")
    seed_metric(
        campaign_id,
        D1,
        impressions=0,
        clicks=0,
        spend_cents=0,
        conversions=0,
    )
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D1.isoformat(),
            "group_by": "campaign",
        },
        headers=partner.headers,
    )
    row = response.json()["data"][0]
    assert row["ctr_pct"] == 0.0
    assert row["cpc_cents"] == 0.0
    assert row["cpm_cents"] == 0.0
    assert row["cvr_pct"] == 0.0


async def test_tenant_isolation(client: Any, report_data: Any, make_partner: Any) -> None:
    _, campaign_a, _ = report_data
    outsider: SeedPartner = make_partner("Outsider", scopes=["reports:read"])
    response = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "campaign_ids": [campaign_a],
        },
        headers=outsider.headers,
    )
    assert response.status_code == 200
    assert response.json()["row_count"] == 0


async def test_invalid_metric_and_date_window(client: Any, report_data: Any) -> None:
    partner, _, _ = report_data
    unknown = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "metrics": "impressions,revenue",
        },
        headers=partner.headers,
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"][0]["field"] == "metrics"

    empty_metrics = await client.get(
        "/v1/reports/metrics",
        params={
            "start_date": D1.isoformat(),
            "end_date": D2.isoformat(),
            "metrics": ",,,",
        },
        headers=partner.headers,
    )
    assert empty_metrics.status_code == 422
    assert empty_metrics.json()["error"]["details"][0]["field"] == "metrics"

    reversed_window = await client.get(
        "/v1/reports/metrics",
        params={"start_date": D2.isoformat(), "end_date": D1.isoformat()},
        headers=partner.headers,
    )
    assert reversed_window.status_code == 422

    too_wide = await client.get(
        "/v1/reports/metrics",
        params={"start_date": "2024-01-01", "end_date": "2026-01-01"},
        headers=partner.headers,
    )
    assert too_wide.status_code == 422
