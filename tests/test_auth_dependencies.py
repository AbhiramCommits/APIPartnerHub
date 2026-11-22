"""The specific API-key and OAuth-bearer dependencies and partner-removal paths."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, FastAPI

from app.auth import AuthContext, api_key_auth, oauth_bearer_auth
from app.db import SessionLocal
from app.errors import APIError, api_error_handler
from app.models import Partner
from tests.conftest import SeedPartner


def build_probe_app() -> FastAPI:
    probe = FastAPI()
    probe.add_exception_handler(APIError, api_error_handler)

    @probe.get("/key")
    def key_route(context: Any = Depends(api_key_auth)) -> dict[str, Any]:
        return {"mode": context.auth_mode, "partner": context.partner.id}

    @probe.get("/oauth")
    def oauth_route(context: Any = Depends(oauth_bearer_auth)) -> dict[str, Any]:
        return {"mode": context.auth_mode, "partner": context.partner.id}

    return probe


async def call_probe(headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_probe_app()), base_url="http://probe"
    ) as client:
        return await client.get("/key", headers=headers)


async def test_api_key_dependency_accepts_only_api_keys(make_partner: Any, client: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    accepted = await call_probe(partner.headers)
    assert accepted.status_code == 200
    assert accepted.json() == {"mode": "api_key", "partner": partner.id}

    token = (
        await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": partner.client_id,
                "client_secret": partner.client_secret,
            },
        )
    ).json()["access_token"]
    rejected = await call_probe({"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401
    assert "X-API-Key" in rejected.json()["error"]["message"]


async def test_oauth_dependency_accepts_only_bearer(make_partner: Any, client: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    token = (
        await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": partner.client_id,
                "client_secret": partner.client_secret,
            },
        )
    ).json()["access_token"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_probe_app()), base_url="http://probe"
    ) as probe:
        accepted = await probe.get("/oauth", headers={"Authorization": f"Bearer {token}"})
        rejected = await probe.get("/oauth", headers=partner.headers)

    assert accepted.status_code == 200
    assert accepted.json()["mode"] == "oauth"
    assert rejected.status_code == 401
    assert "OAuth" in rejected.json()["error"]["message"]


async def test_api_key_with_deleted_partner_is_rejected(make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    with SessionLocal() as session:
        session.query(Partner).filter(Partner.id == partner.id).delete()
        session.commit()
    response = await call_probe(partner.headers)
    assert response.status_code == 401
    assert "not associated" in response.json()["error"]["message"]


async def test_oauth_with_deleted_partner_is_rejected(make_partner: Any, client: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    token = (
        await client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": partner.client_id,
                "client_secret": partner.client_secret,
            },
        )
    ).json()["access_token"]
    with SessionLocal() as session:
        session.query(Partner).filter(Partner.id == partner.id).delete()
        session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_probe_app()), base_url="http://probe"
    ) as probe:
        response = await probe.get("/oauth", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "not associated" in response.json()["error"]["message"]


def test_auth_context_dataclass() -> None:
    partner = Partner(id=1, name="X", tier="free", rate_limit_rpm=60)
    context = AuthContext(
        partner=partner, scopes=["partner:read"], auth_mode="api_key", credential_id="key:abc"
    )
    assert context.jti is None
    assert context.partner.id == 1
