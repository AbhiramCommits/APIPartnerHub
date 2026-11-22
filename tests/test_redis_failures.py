"""Redis outage behaviour for OAuth jti storage/checks and error handlers."""

from __future__ import annotations

from typing import Any

import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.errors import APIError, InternalError, api_error_handler, unhandled_exception_handler
from app.redis_client import set_redis_client
from tests.conftest import SeedPartner


class RedisDown:
    def register_script(self, script: str) -> Any:
        raise redis.RedisError("down")

    def set(self, *args: Any, **kwargs: Any) -> Any:
        raise redis.RedisError("down")

    def exists(self, *args: Any, **kwargs: Any) -> Any:
        raise redis.RedisError("down")


async def test_token_issuance_and_bearer_work_when_redis_is_down(
    client: Any, make_partner: Any
) -> None:
    partner: SeedPartner = make_partner(scopes=["partner:read"])
    set_redis_client(RedisDown())  # type: ignore[arg-type]

    issued = await client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": partner.client_id,
            "client_secret": partner.client_secret,
        },
    )
    assert issued.status_code == 200
    token = issued.json()["access_token"]

    me = await client.get("/v1/partners/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["auth_mode"] == "oauth"


async def test_method_not_allowed_uses_envelope(client: Any) -> None:
    response = await client.post("/healthz")
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_failed"


def test_unhandled_exception_returns_internal_error_envelope() -> None:
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "internal_error"
    assert error["code"] == "INTERNAL-001"
    assert error["request_id"]


def test_api_error_handler_uses_request_state_id() -> None:
    app = FastAPI()
    app.add_exception_handler(APIError, api_error_handler)

    @app.get("/fail")
    def fail(request: Request) -> None:
        request.state.request_id = "from-state"
        raise InternalError("expected")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/fail")
    assert response.status_code == 500, response.text
    assert response.json()["error"]["request_id"] == "from-state"
