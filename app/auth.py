"""Authentication dependencies: API keys, OAuth bearer tokens and scope checks."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
import redis
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import AuthError, ForbiddenScope
from app.models import ApiKey, Partner
from app.redis_client import get_redis
from app.security import (
    decode_access_token,
    missing_scopes,
    parse_api_key,
    scopes_from_csv,
    verify_secret,
)

logger = logging.getLogger("apipartnerhub.auth")

LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


@dataclass(slots=True)
class AuthContext:
    """The authenticated caller for the current request."""

    partner: Partner
    scopes: list[str]
    auth_mode: str  # "api_key" | "oauth"
    credential_id: str
    jti: str | None = None


def _cache_auth(request: Request, context: AuthContext) -> AuthContext:
    request.state.auth_context = context
    request.state.partner_id = context.partner.id
    request.state.auth_mode = context.auth_mode
    return context


def _update_last_used(session: Session, api_key: ApiKey) -> None:
    now = datetime.now(UTC)
    last_used = api_key.last_used_at
    if last_used is not None and last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    if last_used is None or now - last_used > LAST_USED_WRITE_INTERVAL:
        api_key.last_used_at = now
        session.commit()


def _authenticate_api_key(request: Request, session: Session, plaintext: str) -> AuthContext:
    parsed = parse_api_key(plaintext)
    if parsed is None:
        raise AuthError("Malformed API key. Expected the format apph_live_<prefix>_<secret>.")
    prefix, _secret = parsed
    api_key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if api_key is None or not verify_secret(plaintext, api_key.key_hash):
        raise AuthError("Invalid API key.")
    if api_key.revoked_at is not None:
        raise AuthError("This API key has been revoked. Create a new key in the partner portal.")
    partner = session.get(Partner, api_key.partner_id)
    if partner is None:
        raise AuthError("The API key is not associated with an active partner.")
    _update_last_used(session, api_key)
    return _cache_auth(
        request,
        AuthContext(
            partner=partner,
            scopes=scopes_from_csv(api_key.scopes),
            auth_mode="api_key",
            credential_id=f"key:{api_key.prefix}",
        ),
    )


def _authenticate_bearer(request: Request, session: Session, token: str) -> AuthContext:
    try:
        payload: dict[str, Any] = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("OAuth access token has expired. Request a new token.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid OAuth access token.") from exc

    jti = str(payload.get("jti", ""))
    if jti:
        try:
            if not get_redis().exists(f"oauth:jti:{jti}"):
                raise AuthError("OAuth access token has been revoked.")
        except redis.RedisError:
            logger.warning("redis_unavailable_jti_check", extra={"jti": jti})

    partner_id = payload.get("partner_id")
    if not isinstance(partner_id, int):
        raise AuthError("OAuth access token is missing a partner claim.")
    partner = session.get(Partner, partner_id)
    if partner is None:
        raise AuthError("The OAuth client is not associated with an active partner.")
    scopes = str(payload.get("scope", "")).split()
    return _cache_auth(
        request,
        AuthContext(
            partner=partner,
            scopes=scopes,
            auth_mode="oauth",
            credential_id=f"oauth:{payload.get('client_id', 'unknown')}",
            jti=jti or None,
        ),
    )


def authenticate(request: Request, session: Session) -> AuthContext:
    """Resolve either supported auth mode from request headers.

    Used by both the middleware (rate limiting) and the FastAPI dependencies so a request is
    authenticated exactly once.
    """

    cached: AuthContext | None = getattr(request.state, "auth_context", None)
    if cached is not None and isinstance(cached, AuthContext):
        return cached

    api_key = request.headers.get("X-API-Key")
    if api_key:
        return _authenticate_api_key(request, session, api_key)

    authorization = request.headers.get("Authorization", "")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthError("Authorization header must use the Bearer scheme.")
        return _authenticate_bearer(request, session, token)

    raise AuthError(
        "Missing credentials. Send an X-API-Key header or an Authorization: Bearer <token>."
    )


async def get_auth_context(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> AuthContext:
    """FastAPI dependency returning the authenticated caller."""

    return authenticate(request, session)


async def api_key_auth(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> AuthContext:
    """Require API-key authentication specifically."""

    context = authenticate(request, session)
    if context.auth_mode != "api_key":
        raise AuthError("This endpoint requires an X-API-Key credential.")
    return context


async def oauth_bearer_auth(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> AuthContext:
    """Require an OAuth 2.0 bearer token specifically."""

    context = authenticate(request, session)
    if context.auth_mode != "oauth":
        raise AuthError("This endpoint requires an OAuth 2.0 bearer token.")
    return context


ScopeDependency = Callable[..., Awaitable[AuthContext]]


def require_scopes(*required: str) -> ScopeDependency:
    """Build a dependency accepting API key *or* OAuth credentials with all given scopes."""

    async def dependency(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> AuthContext:
        context = authenticate(request, session)
        missing = missing_scopes(context.scopes, list(required))
        if missing:
            raise ForbiddenScope(
                f"Missing required scope(s): {', '.join(missing)}.",
                details=[
                    {"field": "scope", "message": "scope not granted", "value": scope}
                    for scope in missing
                ],
            )
        return context

    return dependency
