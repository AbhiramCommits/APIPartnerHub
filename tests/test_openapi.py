"""OpenAPI generation, security schemes and docs routes."""

from __future__ import annotations

from typing import Any

from app.main import app as fastapi_app


def operations(spec: dict[str, Any]) -> int:
    return sum(len(methods) for methods in spec["paths"].values())


async def test_openapi_spec_has_security_and_operations(client: Any) -> None:
    spec = fastapi_app.openapi()
    assert spec["info"]["title"] == "APIPartnerHub Partner API"
    assert spec["info"]["version"] == "1.0.0"
    assert len(spec["paths"]) == 9
    assert operations(spec) == 11

    schemes = spec["components"]["securitySchemes"]
    assert schemes["ApiKeyAuth"]["in"] == "header"
    assert schemes["ApiKeyAuth"]["name"] == "X-API-Key"
    oauth_scopes = schemes["OAuth2"]["flows"]["clientCredentials"]["scopes"]
    assert "campaigns:write" in oauth_scopes

    for path, methods in spec["paths"].items():
        for _method, operation in methods.items():
            if path == "/healthz" or path == "/oauth/token":
                assert operation["security"] == []
                continue
            assert operation["security"] == [
                {"ApiKeyAuth": []},
                {"OAuth2": [operation["security"][1]["OAuth2"][0]]},
            ]
            assert operation["summary"]
            assert operation["description"]
            assert "401" in operation["responses"]
            assert "429" in operation["responses"]


async def test_openapi_and_docs_are_served(client: Any) -> None:
    spec = await client.get("/openapi.json")
    assert spec.status_code == 200
    assert spec.json()["info"]["version"] == "1.0.0"

    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text.lower()

    redoc = await client.get("/redoc")
    assert redoc.status_code == 200


async def test_required_scope_mapping() -> None:
    from app.main import _required_scope

    assert _required_scope("/v1/campaigns", "get") == "campaigns:read"
    assert _required_scope("/v1/campaigns", "post") == "campaigns:write"
    assert _required_scope("/v1/campaigns/{campaign_id}", "patch") == "campaigns:write"
    assert _required_scope("/v1/reports/metrics", "get") == "reports:read"
    assert _required_scope("/v1/bulk/campaigns", "post") == "bulk:write"
    assert _required_scope("/v1/partners/me", "get") == "partner:read"
