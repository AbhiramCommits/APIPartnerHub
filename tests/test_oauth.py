"""OAuth 2.0 client-credentials issuance and bearer authentication."""

from __future__ import annotations

from typing import Any

from app.redis_client import get_redis
from app.security import create_access_token, decode_access_token
from tests.conftest import SeedPartner


def form(partner: SeedPartner, **overrides: str) -> dict[str, str]:
    payload = {
        "grant_type": "client_credentials",
        "client_id": partner.client_id,
        "client_secret": partner.client_secret,
    }
    payload.update(overrides)
    return payload


async def test_token_issuance(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read", "campaigns:read", "reports:read"])
    response = await client.post("/oauth/token", data=form(partner))
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "campaigns:read partner:read reports:read"

    claims = decode_access_token(body["access_token"])
    assert claims["partner_id"] == partner.id
    assert claims["client_id"] == partner.client_id
    assert claims["scope"] == "campaigns:read partner:read reports:read"
    assert claims["jti"]

    me = await client.get(
        "/v1/partners/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["auth_mode"] == "oauth"


async def test_wrong_secret_uses_rfc_error_shape(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    response = await client.post("/oauth/token", data=form(partner, client_secret="wrong-secret"))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert "access_token" not in response.json()
    assert response.headers["WWW-Authenticate"].startswith("Basic")


async def test_unsupported_grant(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner()
    response = await client.post("/oauth/token", data=form(partner, grant_type="password"))
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


async def test_missing_credentials(client: Any) -> None:
    response = await client.post("/oauth/token", data={"grant_type": "client_credentials"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_invalid_scope(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read"])
    response = await client.post("/oauth/token", data=form(partner, scope="campaigns:write"))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"

    unknown = await client.post("/oauth/token", data=form(partner, scope="not:a:scope"))
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "invalid_scope"


async def test_requested_scope_subset_is_honoured(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read", "reports:read"])
    response = await client.post("/oauth/token", data=form(partner, scope="campaigns:read"))
    token = response.json()["access_token"]
    claims = decode_access_token(token)
    assert claims["scope"] == "campaigns:read"

    campaigns = await client.get("/v1/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert campaigns.status_code == 200
    reports = await client.get(
        "/v1/reports/metrics",
        params={"start_date": "2026-01-01", "end_date": "2026-01-02"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reports.status_code == 403


async def test_expired_token(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    token, jti, _ = create_access_token(
        partner_id=partner.id,
        client_id=partner.client_id,
        scopes=["partner:read"],
        ttl_seconds=-30,
    )
    get_redis().set(f"oauth:jti:{jti}", partner.client_id, ex=60)
    response = await client.get("/v1/partners/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


async def test_revoked_jti(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    issued = await client.post("/oauth/token", data=form(partner))
    token = issued.json()["access_token"]
    claims = decode_access_token(token)
    get_redis().delete(f"oauth:jti:{claims['jti']}")
    response = await client.get("/v1/partners/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "revoked" in response.json()["error"]["message"]


async def test_bearer_scheme_is_required(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    response = await client.get(
        "/v1/partners/me", headers={"Authorization": f"Basic {partner.client_secret}"}
    )
    assert response.status_code == 401
    assert "Bearer" in response.json()["error"]["message"]


async def test_invalid_bearer_token(client: Any) -> None:
    response = await client.get("/v1/partners/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert "Invalid OAuth access token" in response.json()["error"]["message"]
