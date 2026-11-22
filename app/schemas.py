"""Pydantic request/response schemas.

Every model carries field descriptions and examples so the generated Swagger UI and
``openapi.json`` are self-documenting for external partners.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CampaignStatus = Literal["active", "paused", "ended"]
CampaignObjective = Literal["awareness", "consideration", "conversions", "retention"]
Tier = Literal["free", "standard", "enterprise"]

_EXAMPLE_CAMPAIGN: dict[str, Any] = {
    "id": 42,
    "name": "Acme Conversion Blitz 04",
    "status": "active",
    "daily_budget_cents": 75000,
    "objective": "conversions",
    "created_at": "2026-01-14T09:00:00Z",
    "updated_at": "2026-01-15T11:30:00Z",
}


class PartnerProfile(BaseModel):
    """The authenticated partner's profile and entitlements."""

    id: int = Field(description="Stable partner identifier.")
    name: str = Field(description="Display name of the advertiser, agency or integration partner.")
    tier: str = Field(description="Commercial tier: free, standard or enterprise.")
    rate_limit_rpm: int = Field(description="Sustained request budget in requests per minute.")
    rate_limit_burst: int = Field(description="Maximum burst capacity (2x the sustained rate).")
    scopes: list[str] = Field(description="Scopes granted to the credential used for this call.")
    auth_mode: str = Field(description="Authentication mode used: `api_key` or `oauth`.")
    created_at: datetime = Field(description="Partner onboarding timestamp (UTC).")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 3,
                    "name": "Initech Global",
                    "tier": "enterprise",
                    "rate_limit_rpm": 3000,
                    "rate_limit_burst": 6000,
                    "scopes": ["campaigns:read", "campaigns:write", "reports:read"],
                    "auth_mode": "oauth",
                    "created_at": "2026-01-02T08:00:00Z",
                }
            ]
        },
    )


class CampaignCreate(BaseModel):
    """Fields accepted when creating a campaign."""

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable campaign name, shown in every report.",
        examples=["Acme Conversion Blitz 04"],
    )
    status: CampaignStatus = Field(
        default="active", description="Campaign delivery state.", examples=["active"]
    )
    daily_budget_cents: int = Field(
        default=0,
        ge=0,
        le=100_000_000,
        description="Daily budget in minor currency units (cents).",
        examples=[75000],
    )
    objective: CampaignObjective = Field(
        default="awareness",
        description="Optimisation objective.",
        examples=["conversions"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Acme Conversion Blitz 04",
                    "status": "active",
                    "daily_budget_cents": 75000,
                    "objective": "conversions",
                }
            ]
        }
    }


class CampaignUpdate(BaseModel):
    """Partial update. Omitted fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: CampaignStatus | None = None
    daily_budget_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    objective: CampaignObjective | None = None

    model_config = {
        "json_schema_extra": {"examples": [{"status": "paused", "daily_budget_cents": 50000}]}
    }


class CampaignOut(BaseModel):
    """A campaign owned by the authenticated partner."""

    model_config = ConfigDict(
        from_attributes=True, json_schema_extra={"examples": [_EXAMPLE_CAMPAIGN]}
    )

    id: int = Field(description="Campaign identifier (unique within the platform).")
    name: str = Field(description="Campaign name.")
    status: str = Field(description="active, paused or ended.")
    daily_budget_cents: int = Field(description="Daily budget in cents.")
    objective: str = Field(description="awareness, consideration, conversions or retention.")
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime = Field(description="Last modification timestamp; also drives the ETag.")


class CampaignListOut(BaseModel):
    """One page of campaigns plus the cursor for the next page."""

    data: list[CampaignOut] = Field(description="Campaigns on this page.")
    next_cursor: str | None = Field(
        default=None,
        description="Opaque base64 cursor; pass as `cursor` to continue. Null on the last page.",
    )
    has_more: bool = Field(description="True when another page exists.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "data": [_EXAMPLE_CAMPAIGN],
                    "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wMS0xNCIsImlkIjo0Mn0",
                    "has_more": True,
                }
            ]
        }
    }


class MetricRow(BaseModel):
    """Aggregated metrics for one day or one campaign.

    Requested raw metrics are included; derived metrics are always computed server-side.
    """

    date: date_type | None = Field(default=None, description="Day bucket (group_by=day only).")
    campaign_id: int | None = Field(default=None, description="Campaign bucket id.")
    campaign_name: str | None = Field(default=None, description="Campaign bucket name.")
    impressions: int | None = Field(default=None, description="Total impressions.")
    clicks: int | None = Field(default=None, description="Total clicks.")
    spend_cents: int | None = Field(default=None, description="Total spend in cents.")
    conversions: int | None = Field(default=None, description="Total conversions.")
    ctr_pct: float = Field(description="Click-through rate %: clicks / impressions * 100.")
    cpc_cents: float = Field(description="Cost per click in cents: spend_cents / clicks.")
    cpm_cents: float = Field(
        description="Cost per mille in cents: spend_cents / impressions * 1000."
    )
    cvr_pct: float = Field(description="Conversion rate %: conversions / clicks * 100.")


class MetricsResponse(BaseModel):
    """Metric report payload (JSON format)."""

    start_date: date_type = Field(description="Inclusive start of the reported window.")
    end_date: date_type = Field(description="Inclusive end of the reported window.")
    group_by: str = Field(description="Bucket dimension: day or campaign.")
    metrics: list[str] = Field(description="Raw metrics requested and included in each row.")
    row_count: int = Field(description="Number of aggregated rows returned.")
    data: list[MetricRow] = Field(description="Aggregated rows, ordered by bucket.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                    "group_by": "day",
                    "metrics": ["impressions", "clicks", "spend_cents", "conversions"],
                    "row_count": 1,
                    "data": [
                        {
                            "date": "2026-01-01",
                            "impressions": 48213,
                            "clicks": 1203,
                            "spend_cents": 86400,
                            "conversions": 61,
                            "ctr_pct": 2.4952,
                            "cpc_cents": 71.82,
                            "cpm_cents": 1792.03,
                            "cvr_pct": 5.0707,
                        }
                    ],
                }
            ]
        }
    }


class BulkRowError(BaseModel):
    """One validation failure for one CSV row and column."""

    row: int = Field(description="1-based CSV line number, including the header line.")
    field: str = Field(description="Column that failed validation.")
    message: str = Field(description="Human-readable, actionable validation message.")


class BulkResult(BaseModel):
    """Per-row outcome of a CSV campaign import."""

    accepted: int = Field(description="Rows that became campaigns.")
    rejected: int = Field(description="Rows rejected by validation.")
    errors: list[BulkRowError] = Field(description="One entry per rejected field and row.")
    created_ids: list[int] = Field(description="Ids of campaigns created by this import.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "accepted": 4,
                    "rejected": 2,
                    "errors": [
                        {
                            "row": 4,
                            "field": "status",
                            "message": "status must be active, paused or ended",
                        },
                        {
                            "row": 6,
                            "field": "daily_budget_cents",
                            "message": "daily_budget_cents must be a non-negative integer",
                        },
                    ],
                    "created_ids": [26, 27, 28, 29],
                }
            ]
        }
    }
