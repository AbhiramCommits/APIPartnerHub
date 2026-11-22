"""Async Python client for the APIPartnerHub partner API."""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from apipartnerhub._version import __version__
from apipartnerhub.errors import APIConnectionError, APIPartnerHubError
from apipartnerhub.retry import RetryPolicy, SleepFunc, request_with_retries


class SDKResult(dict[str, Any]):
    """Parsed response body with request metadata attached."""

    attempts: int
    status_code: int
    request_id: str | None
    headers: dict[str, str]

    def __init__(
        self,
        data: dict[str, Any],
        *,
        attempts: int,
        status_code: int,
        request_id: str | None,
        headers: dict[str, str],
    ) -> None:
        super().__init__(data)
        self.attempts = attempts
        self.status_code = status_code
        self.request_id = request_id
        self.headers = headers


class APIPartnerHubClient:
    """Async client supporting API keys and OAuth 2.0 client credentials.

    Every method returns plain dictionaries (``SDKResult`` for single-object calls) with
    ``.attempts``, ``.status_code``, ``.request_id`` and ``.headers`` attached. Retries are
    automatic for 429/502/503/504 and connection errors, honouring ``Retry-After``.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        api_key: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30.0,
        max_retries: int | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_func: SleepFunc | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if not api_key and not (client_id and client_secret):
            raise ValueError("Provide api_key or both client_id and client_secret.")
        if api_key and (client_id or client_secret):
            raise ValueError("Use either api_key or OAuth client credentials, not both.")

        if retry_policy is None:
            policy = RetryPolicy(max_retries=max_retries if max_retries is not None else 3)
        else:
            policy = retry_policy
        self.retry_policy = policy
        self._api_key = api_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._sleep = sleep_func
        self._rng = rng
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": f"apipartnerhub-python/{__version__}"},
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self.last_attempts = 0
        self.last_status_code: int | None = None
        self.last_request_id: str | None = None

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> APIPartnerHubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth

    @property
    def uses_oauth(self) -> bool:
        return self._api_key is None

    async def _ensure_token(self, *, force: bool = False) -> str:
        if not force and self._token and time.monotonic() < self._token_expires_at - 60:
            assert self._token is not None
            return self._token
        async with self._token_lock:
            # Another task may have refreshed while this one waited for the lock.
            if not force and self._token and time.monotonic() < self._token_expires_at - 60:
                assert self._token is not None
                return self._token
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        if not self._client_id or not self._client_secret:
            raise ValueError("OAuth client credentials are not configured.")
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }

        async def send() -> httpx.Response:
            return await self._http.post("/oauth/token", data=data)

        try:
            outcome = await request_with_retries(
                send, self.retry_policy, sleep=self._sleep, rng=self._rng
            )
        except httpx.TransportError as exc:
            raise APIConnectionError(f"Could not reach the OAuth token endpoint: {exc}") from exc
        response = outcome.response
        if response.status_code >= 400:
            raise self._error_from_response(response)
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise APIPartnerHubError("OAuth response did not include access_token.")
        self._token = str(token)
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        return self._token

    async def _auth_headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        if self._api_key:
            return {"X-API-Key": self._api_key}
        token = await self._ensure_token(force=force_refresh)
        return {"Authorization": f"Bearer {token}"}

    def _error_from_response(self, response: httpx.Response) -> APIPartnerHubError:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        request_id = response.headers.get("X-Request-ID")
        retry_after = self.retry_policy.retry_after_seconds(response)
        return APIPartnerHubError.from_envelope(
            response.status_code,
            payload if isinstance(payload, dict) else {},
            retry_after=retry_after,
            request_id_header=request_id,
        )

    # ------------------------------------------------------------------ HTTP core

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a raw request and return the httpx response (errors are raised)."""

        headers: dict[str, str] = dict(kwargs.pop("headers", {}) or {})
        force_refresh = False
        response: httpx.Response | None = None

        for auth_attempt in range(2):
            auth_headers = await self._auth_headers(force_refresh=force_refresh)

            async def send(current_headers: dict[str, str] = auth_headers) -> httpx.Response:
                merged = {**current_headers, **headers}
                return await self._http.request(method, path, headers=merged, **kwargs)

            try:
                outcome = await request_with_retries(
                    send, self.retry_policy, sleep=self._sleep, rng=self._rng
                )
            except httpx.TransportError as exc:
                raise APIConnectionError(f"Request failed after retries: {exc}") from exc

            response = outcome.response
            self.last_attempts = outcome.attempts
            self.last_status_code = response.status_code
            self.last_request_id = response.headers.get("X-Request-ID")
            if response.status_code == 401 and self.uses_oauth and auth_attempt == 0:
                force_refresh = True
                continue
            break

        assert response is not None
        if response.status_code >= 400:
            raise self._error_from_response(response)
        return response

    def _result(self, response: httpx.Response) -> SDKResult:
        payload = response.json()
        return SDKResult(
            payload,
            attempts=self.last_attempts,
            status_code=response.status_code,
            request_id=response.headers.get("X-Request-ID"),
            headers=dict(response.headers),
        )

    # ------------------------------------------------------------------ public API

    async def get_me(self) -> SDKResult:
        return self._result(await self.request("GET", "/v1/partners/me"))

    async def list_campaigns(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        objective: str | None = None,
        name_contains: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every campaign, following cursors automatically."""

        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if status:
                params["status"] = status
            if objective:
                params["objective"] = objective
            if name_contains:
                params["name_contains"] = name_contains
            if cursor:
                params["cursor"] = cursor
            response = await self.request("GET", "/v1/campaigns", params=params)
            payload = response.json()
            for campaign in payload.get("data", []):
                yield campaign
            cursor = payload.get("next_cursor")
            if not payload.get("has_more") or not cursor:
                break

    async def create_campaign(
        self,
        name: str | None = None,
        *,
        idempotency_key: str | None = None,
        status: str = "active",
        daily_budget_cents: int = 0,
        objective: str = "awareness",
        payload: dict[str, Any] | None = None,
    ) -> SDKResult:
        body = payload or {
            "name": name,
            "status": status,
            "daily_budget_cents": daily_budget_cents,
            "objective": objective,
        }
        if not body.get("name"):
            raise ValueError("create_campaign requires a name or a payload with 'name'.")
        key = idempotency_key or str(uuid.uuid4())
        response = await self.request(
            "POST", "/v1/campaigns", json=body, headers={"Idempotency-Key": key}
        )
        return self._result(response)

    async def get_campaign(self, campaign_id: int) -> SDKResult:
        return self._result(await self.request("GET", f"/v1/campaigns/{campaign_id}"))

    async def update_campaign(
        self,
        campaign_id: int,
        *,
        if_match: str | None = None,
        **fields: Any,
    ) -> SDKResult:
        if not fields:
            raise ValueError("update_campaign requires at least one field to change.")
        headers = {"If-Match": if_match} if if_match else {}
        response = await self.request(
            "PATCH", f"/v1/campaigns/{campaign_id}", json=fields, headers=headers
        )
        return self._result(response)

    async def get_metrics(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        group_by: str = "day",
        metrics: list[str] | str | None = None,
        campaign_ids: list[int] | None = None,
        as_csv: bool = False,
    ) -> SDKResult | str:
        params: dict[str, Any] = {
            "start_date": start_date.isoformat() if isinstance(start_date, date) else start_date,
            "end_date": end_date.isoformat() if isinstance(end_date, date) else end_date,
            "group_by": group_by,
        }
        if metrics:
            params["metrics"] = ",".join(metrics) if not isinstance(metrics, str) else metrics
        if campaign_ids:
            params["campaign_ids"] = [str(value) for value in campaign_ids]
        if as_csv:
            params["format"] = "csv"
            response = await self.request("GET", "/v1/reports/metrics", params=params)
            return response.text
        return self._result(await self.request("GET", "/v1/reports/metrics", params=params))

    async def bulk_upload_campaigns(
        self, path: str | Path, *, filename: str | None = None
    ) -> SDKResult:
        file_path = Path(path)
        data = file_path.read_bytes()
        files = {"file": (filename or file_path.name, data, "text/csv")}
        response = await self.request("POST", "/v1/bulk/campaigns", files=files)
        return self._result(response)

    async def download_bulk_template(self) -> str:
        response = await self.request("GET", "/v1/bulk/campaigns/template.csv")
        return response.text
