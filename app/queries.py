"""Reusable, partner-scoped query helpers.

Every endpoint that touches campaigns, metrics or idempotency records goes through these
functions so a missing filter cannot leak another partner's data.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Campaign, IdempotencyRecord, MetricDaily


def scoped_campaigns(partner_id: int) -> Select[Any]:
    """Base SELECT that only ever exposes the given partner's campaigns."""

    return select(Campaign).where(Campaign.partner_id == partner_id)


def scoped_metrics(partner_id: int) -> Any:
    """Metrics joined to campaigns and filtered by owner."""

    return (
        select(
            Campaign.id,
            Campaign.name,
            MetricDaily.date,
            MetricDaily.impressions,
            MetricDaily.clicks,
            MetricDaily.spend_cents,
            MetricDaily.conversions,
        )
        .join(MetricDaily, MetricDaily.campaign_id == Campaign.id)
        .where(Campaign.partner_id == partner_id)
    )


def get_campaign_or_404(session: Session, partner_id: int, campaign_id: int) -> Campaign:
    """Fetch one campaign by id *and* owner; 404 otherwise (never reveal existence)."""

    campaign = session.scalar(scoped_campaigns(partner_id).where(Campaign.id == campaign_id))
    if campaign is None:
        raise NotFound(f"Campaign {campaign_id} was not found.")
    return cast(Campaign, campaign)


def scoped_idempotency(
    session: Session, partner_id: int, key: str, endpoint: str
) -> IdempotencyRecord | None:
    return cast(
        IdempotencyRecord | None,
        session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.partner_id == partner_id,
                IdempotencyRecord.key == key,
                IdempotencyRecord.endpoint == endpoint,
            )
        ),
    )
