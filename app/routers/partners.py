"""Partner self-service endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth import AuthContext, require_scopes
from app.schemas import PartnerProfile

router = APIRouter(tags=["Partners"])


@router.get(
    "/partners/me",
    response_model=PartnerProfile,
    summary="Get the authenticated partner",
    description=(
        "Returns the caller's profile, commercial tier, effective rate limit (steady and "
        "burst), granted scopes and the auth mode used for this request."
    ),
)
def get_me(
    request: Request,
    context: Annotated[AuthContext, Depends(require_scopes("partner:read"))],
) -> PartnerProfile:
    partner = context.partner
    return PartnerProfile(
        id=partner.id,
        name=partner.name,
        tier=partner.tier,
        rate_limit_rpm=partner.rate_limit_rpm,
        rate_limit_burst=2 * partner.rate_limit_rpm,
        scopes=context.scopes,
        auth_mode=context.auth_mode,
        created_at=partner.created_at,
    )
