"""OAuth 2.0 client-credentials token endpoint (RFC 6749 section 4.4)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import OAuthClient
from app.redis_client import get_redis
from app.security import ALL_SCOPES, create_access_token, scopes_from_csv, verify_secret

logger = logging.getLogger("apipartnerhub.oauth")
settings = get_settings()

router = APIRouter(tags=["OAuth"])


class TokenResponse(BaseModel):
    access_token: str = Field(description="Signed JWT to send as `Authorization: Bearer <token>`.")
    token_type: str = Field(default="bearer", description="Always `bearer`.")
    expires_in: int = Field(description="Token lifetime in seconds.")
    scope: str = Field(description="Space-delimited scopes granted to the token.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "scope": "campaigns:read campaigns:write reports:read",
                }
            ]
        }
    }


def _rfc_error(
    error: str,
    description: str,
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """OAuth errors use the RFC 6749 shape, not the API error envelope."""

    payload: dict[str, Any] = {"error": error, "error_description": description}
    return JSONResponse(status_code=status_code, content=payload, headers=headers or {})


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    summary="Exchange client credentials for an access token",
    description=(
        "RFC 6749 client-credentials grant. Send `application/x-www-form-urlencoded` fields "
        "`grant_type=client_credentials`, `client_id`, `client_secret` and optionally `scope`."
    ),
    responses={
        400: {"description": "unsupported_grant_type or invalid_scope (RFC 6749 shape)"},
        401: {"description": "invalid_client (RFC 6749 shape)"},
    },
)
async def issue_token(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> TokenResponse | JSONResponse:
    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    client_id = str(form.get("client_id") or "")
    client_secret = str(form.get("client_secret") or "")
    scope_param = form.get("scope")

    if not client_id or not client_secret:
        return _rfc_error("invalid_request", "client_id and client_secret are required.", 400)
    if grant_type != "client_credentials":
        return _rfc_error(
            "unsupported_grant_type",
            "Only the client_credentials grant is supported.",
            400,
        )

    client = session.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if client is None or not verify_secret(client_secret, client.client_secret_hash):
        logger.info("oauth_invalid_client", extra={"client_id": client_id})
        return _rfc_error(
            "invalid_client",
            "Client authentication failed.",
            401,
            headers={"WWW-Authenticate": 'Basic realm="oauth"'},
        )

    granted = scopes_from_csv(client.scopes)
    if scope_param:
        requested = sorted(
            {
                part
                for chunk in str(scope_param).replace(",", " ").split()
                for part in [chunk.strip()]
                if part
            }
        )
        invalid = [scope for scope in requested if scope not in ALL_SCOPES]
        if invalid:
            return _rfc_error("invalid_scope", f"Unknown scope(s): {', '.join(invalid)}.", 400)
        not_granted = [scope for scope in requested if scope not in granted]
        if not_granted:
            return _rfc_error(
                "invalid_scope",
                f"Scope(s) not granted to this client: {', '.join(not_granted)}.",
                400,
            )
        granted = requested

    token, jti, expires_in = create_access_token(
        partner_id=client.partner_id,
        client_id=client.client_id,
        scopes=granted,
    )
    try:
        get_redis().set(f"oauth:jti:{jti}", client.client_id, ex=expires_in)
    except redis.RedisError:
        logger.warning("redis_unavailable_jti_store", extra={"jti": jti})

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        scope=" ".join(granted),
    )
