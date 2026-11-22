# APIPartnerHub Python SDK

Async Python client for the APIPartnerHub partner API with automatic cursor pagination,
idempotent creates, CSV upload/download and retry/backoff that honours `Retry-After`.

## Install

```bash
pip install -e sdks/python
```

Requires Python 3.10+ and `httpx`.

## 10-line quickstart

```python
import asyncio
from apipartnerhub import APIPartnerHubClient


async def main():
    async with APIPartnerHubClient("http://localhost:8000", api_key="apph_live_...") as client:
        me = await client.get_me()
        print(me["name"], me["tier"], me["rate_limit_rpm"])
        async for campaign in client.list_campaigns(limit=25):
            print(campaign["id"], campaign["name"])


asyncio.run(main())
```

OAuth works the same way; tokens are fetched and refreshed automatically:

```python
client = APIPartnerHubClient("http://localhost:8000", client_id="apc_...", client_secret="...")
```

## Highlights

- `list_campaigns()` is an async iterator that follows cursors until `has_more` is false.
- `create_campaign()` sends an `Idempotency-Key` (generated if omitted), so retries are safe.
- `update_campaign(id, if_match=etag, status="paused")` uses optimistic concurrency.
- `get_metrics(start, end, as_csv=True)` returns the raw CSV text.
- Retries 429/502/503/504 and transport errors up to `max_retries` with full-jitter backoff;
  429 delays use the server's `Retry-After` exactly.
- Typed exceptions (`RateLimitedError.retry_after`, `ValidationError.details`, every error's
  `.request_id`) map 1:1 from the API error envelope.
- Each single-object result is an `SDKResult` dict carrying `.attempts`, `.status_code`,
  `.request_id` and `.headers`.

See `examples/python/` and `docs/integration-guide.md` for runnable usage.
