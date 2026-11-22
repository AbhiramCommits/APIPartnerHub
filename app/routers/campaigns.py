"""Campaign CRUD: cursor pagination, idempotent creates and ETag-guarded updates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_scopes
from app.db import get_session
from app.errors import ConflictError, ValidationFailed
from app.models import Campaign, IdempotencyRecord, utcnow
from app.pagination import decode_cursor, encode_cursor
from app.queries import get_campaign_or_404, scoped_campaigns, scoped_idempotency
from app.schemas import (
    CampaignCreate,
    CampaignListOut,
    CampaignObjective,
    CampaignOut,
    CampaignStatus,
    CampaignUpdate,
)

router = APIRouter(tags=["Campaigns"])

CREATE_ENDPOINT = "POST /v1/campaigns"


def campaign_etag(updated_at: datetime) -> str:
    """Strong ETag derived from the update timestamp (microsecond precision)."""

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return f'"{int(updated_at.timestamp() * 1_000_000)}"'


def _request_hash(payload: CampaignCreate) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay(record: IdempotencyRecord) -> JSONResponse:
    return JSONResponse(
        status_code=record.status_code,
        content=json.loads(record.response_json),
        headers={
            "Idempotency-Replayed": "true",
            "Content-Type": "application/json",
        },
    )


@router.get(
    "/campaigns",
    response_model=CampaignListOut,
    summary="List campaigns",
    description=(
        "Cursor-paginated campaign list scoped to the authenticated partner. "
        "Pages are ordered by `created_at` descending, then id descending. Pass the opaque "
        "`next_cursor` back as `cursor` until `has_more` is false."
    ),
)
async def list_campaigns(
    context: Annotated[AuthContext, Depends(require_scopes("campaigns:read"))],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100, description="Page size (max 100).")] = 25,
    cursor: Annotated[
        str | None, Query(max_length=512, description="Opaque cursor from `next_cursor`.")
    ] = None,
    status: Annotated[
        CampaignStatus | None, Query(description="Filter by delivery status.")
    ] = None,
    objective: Annotated[
        CampaignObjective | None, Query(description="Filter by optimization objective.")
    ] = None,
    name_contains: Annotated[
        str | None, Query(max_length=100, description="Case-insensitive substring match on name.")
    ] = None,
) -> CampaignListOut:
    stmt = scoped_campaigns(context.partner.id)
    if status is not None:
        stmt = stmt.where(Campaign.status == status)
    if objective is not None:
        stmt = stmt.where(Campaign.objective == objective)
    if name_contains:
        stmt = stmt.where(Campaign.name.ilike(f"%{name_contains}%"))
    if cursor:
        created_at, campaign_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Campaign.created_at < created_at,
                and_(Campaign.created_at == created_at, Campaign.id < campaign_id),
            )
        )
    stmt = stmt.order_by(Campaign.created_at.desc(), Campaign.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return CampaignListOut(
        data=[CampaignOut.model_validate(row) for row in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/campaigns",
    response_model=CampaignOut,
    status_code=201,
    summary="Create a campaign",
    description=(
        "Creates a campaign for the authenticated partner. Send an optional "
        "`Idempotency-Key` header; replaying the same key with the identical body returns "
        "the original response and `Idempotency-Replayed: true`. Reusing the key with a "
        "different body returns 409."
    ),
    responses={
        200: {"description": "Idempotent replay of a previously created campaign."},
        409: {"description": "Idempotency-Key reused with a different request body."},
    },
)
async def create_campaign(
    payload: CampaignCreate,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_scopes("campaigns:write"))],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Client-generated key (<=255 chars) that makes this POST safely retryable.",
        ),
    ] = None,
) -> CampaignOut | JSONResponse:
    key = idempotency_key.strip() if idempotency_key else None
    if key and len(key) > 255:
        raise ValidationFailed(
            "Idempotency-Key is too long.",
            details=[{"field": "Idempotency-Key", "message": "Maximum length is 255 characters."}],
        )
    body_hash = _request_hash(payload)

    if key:
        existing = scoped_idempotency(session, context.partner.id, key, CREATE_ENDPOINT)
        if existing is not None:
            if existing.request_hash != body_hash:
                raise ConflictError(
                    "This Idempotency-Key was already used with a different request body.",
                    details=[{"field": "Idempotency-Key", "message": "body hash mismatch"}],
                )
            return _replay(existing)

    campaign = Campaign(
        partner_id=context.partner.id,
        name=payload.name.strip(),
        status=payload.status,
        daily_budget_cents=payload.daily_budget_cents,
        objective=payload.objective,
    )
    session.add(campaign)
    try:
        session.flush()
    except IntegrityError as exc:  # pragma: no cover - defensive
        session.rollback()
        raise ConflictError("Could not create campaign.") from exc

    out = CampaignOut.model_validate(campaign)
    if key:
        session.add(
            IdempotencyRecord(
                key=key,
                partner_id=context.partner.id,
                endpoint=CREATE_ENDPOINT,
                request_hash=body_hash,
                status_code=201,
                response_json=out.model_dump_json(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = scoped_idempotency(session, context.partner.id, key, CREATE_ENDPOINT)
            if existing is not None:
                if existing.request_hash != body_hash:
                    raise ConflictError(
                        "This Idempotency-Key was already used with a different request body.",
                    ) from None
                return _replay(existing)
            raise
    else:
        session.commit()

    response.headers["Location"] = f"/v1/campaigns/{campaign.id}"
    response.headers["ETag"] = campaign_etag(campaign.updated_at)
    return out


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="Get one campaign",
    description="Returns the campaign if it belongs to the authenticated partner; otherwise 404.",
    responses={404: {"description": "Campaign not found for this partner."}},
)
async def get_campaign(
    campaign_id: int,
    context: Annotated[AuthContext, Depends(require_scopes("campaigns:read"))],
    session: Annotated[Session, Depends(get_session)],
    response: Response,
) -> CampaignOut:
    campaign = get_campaign_or_404(session, context.partner.id, campaign_id)
    response.headers["ETag"] = campaign_etag(campaign.updated_at)
    return CampaignOut.model_validate(campaign)


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    summary="Partially update a campaign",
    description=(
        "Applies only the provided fields. If `If-Match` is sent it must equal the current "
        "ETag from GET or a previous PATCH, otherwise the update is rejected with 409 so "
        "concurrent writers cannot silently overwrite each other."
    ),
    responses={
        404: {"description": "Campaign not found for this partner."},
        409: {"description": "If-Match conflicted with the current ETag."},
    },
)
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    context: Annotated[AuthContext, Depends(require_scopes("campaigns:write"))],
    session: Annotated[Session, Depends(get_session)],
    response: Response,
    if_match: Annotated[
        str | None,
        Header(alias="If-Match", description="Current ETag; `*` matches any existing resource."),
    ] = None,
) -> CampaignOut:
    campaign = get_campaign_or_404(session, context.partner.id, campaign_id)
    current_etag = campaign_etag(campaign.updated_at)
    if if_match is not None and if_match.strip() not in (current_etag, "*"):
        raise ConflictError(
            "If-Match precondition failed: the campaign changed since you read it.",
            details=[
                {
                    "field": "If-Match",
                    "message": "Fetch the campaign again and retry with the new ETag.",
                    "value": current_etag,
                }
            ],
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise ValidationFailed(
            "No fields to update.",
            details=[{"field": "body", "message": "Provide at least one updatable field."}],
        )
    for field, value in updates.items():
        if value is None:
            raise ValidationFailed(
                f"Field '{field}' cannot be null.",
                details=[{"field": field, "message": "Omit the field instead of sending null."}],
            )
        setattr(campaign, field, value)
    campaign.updated_at = utcnow()
    session.commit()

    new_etag = campaign_etag(campaign.updated_at)
    response.headers["ETag"] = new_etag
    return CampaignOut.model_validate(campaign)
