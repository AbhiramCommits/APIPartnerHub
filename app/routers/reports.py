"""Aggregated performance reporting with server-side metric math and streamed CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_scopes
from app.db import get_session
from app.errors import ValidationFailed
from app.models import Campaign, MetricDaily
from app.queries import scoped_metrics
from app.schemas import MetricRow, MetricsResponse

router = APIRouter(tags=["Reports"])

RAW_METRICS = ["impressions", "clicks", "spend_cents", "conversions"]
DERIVED_METRICS = ["ctr_pct", "cpc_cents", "cpm_cents", "cvr_pct"]


def _ratio(numerator: float, denominator: float, multiplier: float = 1.0) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * multiplier, 4)


def _derived(impressions: int, clicks: int, spend_cents: int, conversions: int) -> dict[str, float]:
    return {
        "ctr_pct": _ratio(clicks, impressions, 100.0),
        "cpc_cents": _ratio(spend_cents, clicks),
        "cpm_cents": _ratio(spend_cents, impressions, 1000.0),
        "cvr_pct": _ratio(conversions, clicks, 100.0),
    }


def _parse_metrics(metrics: str) -> list[str]:
    requested = [part.strip() for part in metrics.split(",") if part.strip()]
    if not requested:
        raise ValidationFailed(
            "At least one metric is required.",
            details=[{"field": "metrics", "message": "Pass a comma-separated metric list."}],
        )
    unknown = [name for name in requested if name not in RAW_METRICS]
    if unknown:
        raise ValidationFailed(
            f"Unknown metric(s): {', '.join(unknown)}.",
            details=[
                {"field": "metrics", "message": f"Allowed metrics: {', '.join(RAW_METRICS)}."}
            ],
        )
    return [name for name in RAW_METRICS if name in requested]


@router.get(
    "/reports/metrics",
    response_model=MetricsResponse,
    response_model_exclude_none=True,
    summary="Aggregate campaign metrics",
    description=(
        "Aggregates daily metrics for the caller's campaigns over an inclusive date window. "
        "Group by `day` or `campaign`. Raw metrics are selected with `metrics`; CTR, CPC, CPM "
        "and CVR are always computed server-side. Use `format=csv` for a streamed download."
    ),
    responses={
        200: {
            "description": "JSON report, or a CSV stream when format=csv.",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        },
        422: {"description": "Invalid metrics, dates or date window."},
    },
)
def get_metrics(
    context: Annotated[AuthContext, Depends(require_scopes("reports:read"))],
    session: Annotated[Session, Depends(get_session)],
    start_date: Annotated[date, Query(description="Inclusive start date (YYYY-MM-DD).")],
    end_date: Annotated[date, Query(description="Inclusive end date (YYYY-MM-DD).")],
    group_by: Annotated[
        Literal["day", "campaign"], Query(description="Aggregation bucket.")
    ] = "day",
    metrics: Annotated[
        str,
        Query(
            description=(
                "Comma-separated raw metrics: impressions, clicks, spend_cents, conversions."
            )
        ),
    ] = "impressions,clicks,spend_cents,conversions",
    campaign_ids: Annotated[
        list[int] | None,
        Query(description="Optional campaign id filter. Repeat the parameter for multiple ids."),
    ] = None,
    format: Annotated[Literal["json", "csv"], Query(description="Response format.")] = "json",
) -> MetricsResponse | StreamingResponse:
    if start_date > end_date:
        raise ValidationFailed(
            "start_date must be on or before end_date.",
            details=[{"field": "start_date", "message": "Start date is after end date."}],
        )
    if (end_date - start_date).days > 366:
        raise ValidationFailed(
            "Date window is too large.",
            details=[{"field": "end_date", "message": "Maximum window is 366 days."}],
        )
    selected = _parse_metrics(metrics)

    base_stmt = scoped_metrics(context.partner.id).where(
        MetricDaily.date >= start_date,
        MetricDaily.date <= end_date,
        Campaign.partner_id == context.partner.id,
    )
    if campaign_ids:
        base_stmt = base_stmt.where(Campaign.id.in_(campaign_ids))
    subq = base_stmt.subquery()

    sums = [
        func.coalesce(func.sum(subq.c.impressions), 0).label("impressions"),
        func.coalesce(func.sum(subq.c.clicks), 0).label("clicks"),
        func.coalesce(func.sum(subq.c.spend_cents), 0).label("spend_cents"),
        func.coalesce(func.sum(subq.c.conversions), 0).label("conversions"),
    ]

    rows: list[dict[str, Any]] = []
    if group_by == "day":
        stmt = (
            select(subq.c.date, *sums)
            .select_from(subq)
            .group_by(subq.c.date)
            .order_by(subq.c.date.asc())
        )
        for record in session.execute(stmt).mappings():
            row: dict[str, Any] = {
                "date": record["date"],
                **_bucket_values(record, selected),
            }
            rows.append(row)
    else:
        stmt = (
            select(subq.c.id, subq.c.name, *sums)
            .select_from(subq)
            .group_by(subq.c.id, subq.c.name)
            .order_by(func.sum(subq.c.impressions).desc(), subq.c.id.asc())
        )
        for record in session.execute(stmt).mappings():
            row = {
                "campaign_id": int(record["id"]),
                "campaign_name": record["name"],
                **_bucket_values(record, selected),
            }
            rows.append(row)

    parsed_rows = [MetricRow.model_validate(row) for row in rows]

    if format == "csv":
        dimension = ["date"] if group_by == "day" else ["campaign_id", "campaign_name"]
        fieldnames = dimension + selected + DERIVED_METRICS
        csv_rows = [row.model_dump(mode="json") for row in parsed_rows]
        return StreamingResponse(
            _csv_stream(fieldnames, csv_rows),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="metrics_{start_date}_{end_date}_by_{group_by}.csv"'
                )
            },
        )

    return MetricsResponse(
        start_date=start_date,
        end_date=end_date,
        group_by=group_by,
        metrics=selected,
        row_count=len(parsed_rows),
        data=parsed_rows,
    )


def _bucket_values(record: Any, selected: list[str]) -> dict[str, Any]:
    totals = {
        "impressions": int(record["impressions"]),
        "clicks": int(record["clicks"]),
        "spend_cents": int(record["spend_cents"]),
        "conversions": int(record["conversions"]),
    }
    values: dict[str, Any] = {name: totals[name] for name in selected}
    values.update(_derived(**totals))
    return values


def _csv_stream(fieldnames: list[str], rows: list[dict[str, Any]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue()
    for row in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow({key: row.get(key, "") for key in fieldnames})
        yield buffer.getvalue()
