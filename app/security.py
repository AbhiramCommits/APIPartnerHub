"""Credential generation, hashing, scopes and JWT helpers."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

logger = logging.getLogger("apipartnerhub.security")
settings = get_settings()

API_KEY_PREFIX = "apph_live"
OAUTH_CLIENT_PREFIX = "apc"
ALL_SCOPES = [
    "partner:read",
    "campaigns:read",
    "campaigns:write",
    "reports:read",
    "bulk:write",
]
_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def hash_secret(secret: str) -> str:
    """Hash an API key secret or OAuth client secret with Argon2id."""

    return _PASSWORD_HASHER.hash(secret)


def verify_secret(secret: str, secret_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(secret_hash, secret)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    """Create an API key.

    Returns ``(prefix, plaintext, hash)``. The plaintext is shown to the partner exactly once
    and only the Argon2 hash is stored.
    """

    prefix = secrets.token_hex(6)
    plaintext = f"{API_KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    return prefix, plaintext, hash_secret(plaintext)


def parse_api_key(plaintext: str) -> tuple[str, str] | None:
    """Split ``apph_live_<prefix>_<secret>`` into its prefix and secret."""

    if not plaintext.startswith(f"{API_KEY_PREFIX}_"):
        return None
    remainder = plaintext[len(API_KEY_PREFIX) + 1 :]
    if "_" not in remainder:
        return None
    prefix, secret = remainder.split("_", 1)
    if not prefix or not secret:
        return None
    return prefix, secret


def generate_oauth_client() -> tuple[str, str, str]:
    """Create an OAuth client id/secret pair plus the hash of the secret."""

    client_id = f"{OAUTH_CLIENT_PREFIX}_{secrets.token_hex(12)}"
    client_secret = secrets.token_urlsafe(32)
    return client_id, client_secret, hash_secret(client_secret)


def scopes_to_csv(scopes: list[str]) -> str:
    return ",".join(sorted(set(scopes)))


def scopes_from_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({part.strip() for part in value.split(",") if part.strip()})


def missing_scopes(granted: list[str], required: list[str]) -> list[str]:
    granted_set = set(granted)
    return [scope for scope in required if scope not in granted_set]


def create_access_token(
    *,
    partner_id: int,
    client_id: str,
    scopes: list[str],
    ttl_seconds: int | None = None,
    jti: str | None = None,
) -> tuple[str, str, int]:
    """Sign a JWT access token. Returns ``(token, jti, expires_in)``."""

    expires_in = ttl_seconds if ttl_seconds is not None else settings.JWT_TTL_SECONDS
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=expires_in)
    token_jti = jti or uuid.uuid4().hex
    payload: dict[str, Any] = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": f"partner:{partner_id}",
        "partner_id": partner_id,
        "client_id": client_id,
        "scope": " ".join(sorted(set(scopes))),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": token_jti,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, token_jti, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises ``jwt.PyJWTError`` on any problem."""

    decoded: dict[str, Any] = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "jti"]},
    )
    return decoded
