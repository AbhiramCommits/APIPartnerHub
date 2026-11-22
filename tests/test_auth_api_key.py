"""API key authentication and scope enforcement."""

from __future__ import annotations

from typing import Any

from tests.conftest import SeedPartner, revoke_key


async def test_valid_api_key(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(
        "Acme", tier="free", scopes=["partner:read", "campaigns:read"]
    )
    response = await client.get("/v1/partners/me", headers=partner.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == partner.id
    assert body["auth_mode"] == "api_key"
    assert body["tier"] == "free"
    assert body["rate_limit_burst"] == partner.rpm * 2
    assert body["scopes"] == ["campaigns:read", "partner:read"]
    assert response.headers["X-Request-ID"]


async def test_bad_api_key(client: Any) -> None:
    response = await client.get(
        "/v1/partners/me", headers={"X-API-Key": "apph_live_deadbeef_invalid-secret"}
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["type"] == "authentication_failed"
    assert error["code"] == "AUTH-001"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_malformed_api_key(client: Any) -> None:
    response = await client.get("/v1/partners/me", headers={"X-API-Key": "not-a-key"})
    assert response.status_code == 401
    assert "Malformed API key" in response.json()["error"]["message"]


async def test_missing_header(client: Any) -> None:
    response = await client.get("/v1/partners/me")
    assert response.status_code == 401
    assert "Missing credentials" in response.json()["error"]["message"]


async def test_revoked_api_key(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    revoke_key(partner.api_key)
    response = await client.get("/v1/partners/me", headers=partner.headers)
    assert response.status_code == 401
    assert "revoked" in response.json()["error"]["message"]


async def test_scope_denial(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read", "campaigns:read"])
    response = await client.post(
        "/v1/campaigns",
        headers=partner.headers,
        json={"name": "Should fail", "daily_budget_cents": 1},
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["type"] == "insufficient_scope"
    assert error["code"] == "SCOPE-001"
    assert error["details"][0]["value"] == "campaigns:write"


async def test_api_key_last_used_is_recorded(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    await client.get("/v1/partners/me", headers=partner.headers)

    from app.db import SessionLocal
    from app.models import ApiKey
    from app.security import parse_api_key

    parsed = parse_api_key(partner.api_key)
    assert parsed is not None
    with SessionLocal() as session:
        key = session.query(ApiKey).filter(ApiKey.prefix == parsed[0]).one()
        assert key.last_used_at is not None
