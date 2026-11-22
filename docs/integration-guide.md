# Partner integration guide

This is the onboarding document for advertisers, agencies and integration partners building on
APIPartnerHub. It covers credentials, the first call, pagination, idempotency, rate limits,
retries, errors, CSV workflows and a go-live checklist.

Local base URL: `http://localhost:8000` (this repo's machine-local `.env` uses `:8010` because
8000 was already in use; substitute your own `BASE_URL`). Interactive reference: `/docs`.

## 1. Get credentials

Run `python -m scripts.seed` (or `make seed`) once. It prints the credentials for three demo
partners and writes them to `.seed_credentials.json` (gitignored):

| tier | partner | steady rate | burst | scopes |
|---|---|---|---|---|
| free | Acme Retail | 60 rpm | 120 | `partner:read`, `campaigns:read`, `reports:read` |
| standard | Globex Media | 600 rpm | 1200 | free + `campaigns:write`, `bulk:write` |
| enterprise | Initech Global | 3000 rpm | 6000 | same as standard |

Each partner has one long-lived API key (`apph_live_<prefix>_<secret>`) and one OAuth client
(`apc_...` client id + secret). In production these come from the partner portal; the plaintext
key/secret is shown exactly once and only an Argon2 hash is stored server-side.

## 2. API key or OAuth?

| | API key | OAuth 2.0 client credentials |
|---|---|---|
| Best for | Server-to-server integrations that can store a secret securely | Integrations that need short-lived credentials, central rotation, or per-service tokens |
| Lifetime | Long-lived until revoked | 3600 s by default (`JWT_TTL_SECONDS`) |
| Rotation | Issue the new key, deploy, then revoke the old one | Secrets rotate independently; tokens expire automatically |
| Transport | `X-API-Key: apph_live_...` | `Authorization: Bearer <jwt>` |
| Scopes | Bound to the key at issuance | Requested at token mint time, subset of the client's grants |
| Revocation | Set `revoked_at` on the key | `jti` is checked against Redis on every request |

Both modes are accepted on every `/v1` endpoint; the required scope is enforced regardless of
mode.

Mint a token:

```bash
curl -sS -X POST "$BASE_URL/oauth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id="$CLIENT_ID" \
  --data-urlencode client_secret="$CLIENT_SECRET" \
  --data-urlencode 'scope=campaigns:read reports:read'
```

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600,
  "scope": "campaigns:read reports:read"
}
```

Wrong credentials return the RFC 6749 shape, not the API envelope:

```json
{"error": "invalid_client", "error_description": "Client authentication failed."}
```

## 3. First call

```bash
curl -sS "$BASE_URL/v1/partners/me" -H "X-API-Key: $API_KEY" | jq .
```

```json
{
  "id": 1,
  "name": "Acme Retail",
  "tier": "free",
  "rate_limit_rpm": 60,
  "rate_limit_burst": 120,
  "scopes": ["campaigns:read", "partner:read", "reports:read"],
  "auth_mode": "api_key",
  "created_at": "2026-11-22T06:47:33.855031Z"
}
```

Python:

```python
import asyncio
from apipartnerhub import APIPartnerHubClient


async def main():
    async with APIPartnerHubClient("http://localhost:8000", api_key="apph_live_...") as client:
        me = await client.get_me()
        print(me["name"], me["tier"])


asyncio.run(main())
```

TypeScript:

```ts
import { APIPartnerHubClient } from "@apipartnerhub/sdk";

const client = new APIPartnerHubClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.API_KEY!,
});
console.log(await client.getMe());
```

## 4. Pagination contract

`GET /v1/campaigns` returns:

```json
{"data": [...], "next_cursor": "eyJjcmVhdGVkX2F0IjoiLi4uIiwiaWQiOjEwfQ", "has_more": true}
```

- `limit` is 1–100, default 25.
- Ordering is `created_at DESC, id DESC` and is stable for a given dataset snapshot.
- `next_cursor` is opaque base64 — do not parse or construct it.
- Continue passing it as `cursor` until `has_more` is `false`; `next_cursor` is then `null`.
- A malformed cursor is a 422 `validation_failed` with `details[0].field == "cursor"`; restart
  without a cursor.
- The cursor encodes the last `(created_at, id)` pair, so new campaigns created between pages do
  not shift the page you are reading and rows are not duplicated within a walk.
- Filters (`status`, `objective`, `name_contains`) must stay identical across the walk.

The Python SDK follows cursors for you:

```python
async for campaign in client.list_campaigns(limit=100):
    process(campaign)
```

Node:

```ts
for await (const campaign of client.listCampaigns({ limit: 100 })) {
  process(campaign);
}
```

## 5. Idempotency contract

`POST /v1/campaigns` accepts an `Idempotency-Key` request header (≤255 chars). The key is scoped
to the partner and endpoint.

- First call: creates the campaign, returns 201 with `Location` and `ETag`.
- Replay with the identical JSON body: returns the **same status and body**, plus
  `Idempotency-Replayed: true`. No duplicate is created.
- Same key with a different body: 409 `conflict` (`CONFLICT-001`), with the field in `details`.
- Omitting the header means no replay protection. The Python SDK generates a UUID
  automatically; the TypeScript SDK does too. Persist the key with your own request record if
  you need to replay after a process restart.
- Responses are stored with the key; there is no automatic TTL yet (see README limitations), so
  use a bounded key namespace (e.g. one per business operation attempt).

PATCH uses optimistic concurrency instead: send `If-Match` with the ETag from GET. A stale ETag
is 409 with `details[0].field == "If-Match"`; re-GET and retry.

## 6. Rate limits per tier

The limiter is a **per-partner token bucket** in Redis: steady refill = tier RPM, capacity =
2×RPM. Consuming 120 requests instantly on the free tier is allowed; after that, one token
returns per second.

Every `/v1` response carries:

| header | meaning |
|---|---|
| `RateLimit-Limit` | Burst capacity (2 × RPM). |
| `RateLimit-Remaining` | Whole tokens left after this request. |
| `RateLimit-Reset` | Seconds until the bucket is full. |
| `Retry-After` | 429 only: seconds until one token is available (≥1). |

A 429 body uses the standard envelope:

```json
{
  "error": {
    "type": "rate_limited",
    "message": "Rate limit exceeded. Retry after the number of seconds in Retry-After.",
    "code": "RL-001",
    "doc_url": "https://docs.apipartnerhub.dev/errors#rate_limited",
    "request_id": "ba33489645ef4e12831cdba99d4a3f7a",
    "details": [{"field": "rate_limit", "message": "Limit 120 requests per burst window; retry after the indicated delay."}]
  }
}
```

If Redis is unavailable the limiter fails open (serves traffic and logs a WARNING); plan for
occasional over-delivery rather than treating the headers as a hard security boundary.

## 7. Retry and backoff

Retry only 429, 502, 503, 504 and connection errors. Honor `Retry-After` exactly when present;
otherwise use exponential backoff with full jitter. Cap retries (3–5) and the delay (8 s), and
never retry non-idempotent POSTs unless you send an `Idempotency-Key`.

Python SDK (built in — nothing to configure):

```python
from apipartnerhub import APIPartnerHubClient, RetryPolicy

client = APIPartnerHubClient(
    "http://localhost:8000",
    api_key=API_KEY,
    retry_policy=RetryPolicy(max_retries=3, base_delay=0.5, max_delay=8.0),
)
```

Raw implementation:

```python
import asyncio, random


async def backoff(attempt: int, retry_after: float | None) -> None:
    if retry_after is not None:
        await asyncio.sleep(retry_after)
        return
    ceiling = min(8.0, 0.5 * 2**attempt)
    await asyncio.sleep(random.uniform(0, ceiling))  # full jitter
```

TypeScript SDK: `fetchWithRetry` in `@apipartnerhub/sdk` does the same and exposes the attempt
count (`client.lastAttempts`). For custom code:

```ts
const delayMs = retryAfter
  ? Number(retryAfter) * 1000
  : Math.random() * Math.min(8000, 500 * 2 ** attempt);
await new Promise((resolve) => setTimeout(resolve, delayMs));
```

## 8. Error handling

Every API error (except `/oauth/token`) is the same envelope with `type`, `message`, `code`,
`doc_url`, `request_id` and `details`. Branch on `type` (stable), quote `code` + `request_id` in
support tickets, and read `details[].field` for per-field or per-row failures. Full table:
[`docs/errors.md`](errors.md).

SDK exception mapping:

| status | Python | TypeScript |
|---|---|---|
| 401 | `AuthenticationError` | `AuthenticationError` |
| 403 | `ForbiddenError` | `ForbiddenError` |
| 404 | `NotFoundError` | `NotFoundError` |
| 409 | `ConflictError` | `ConflictError` |
| 422 | `ValidationError` (row-level `details`) | `ValidationError` |
| 429 | `RateLimitedError` (`.retry_after`) | `RateLimitedError` (`.retryAfter`) |
| 5xx | `ServerError` | `ServerError` |

Every exception has `.request_id` / `.requestId` for escalation. Never retry a 401/403/404/422
without changing the request.

## 9. CSV field reference

`POST /v1/bulk/campaigns` (multipart field `file`, UTF-8, ≤5 MB):

| column | required | rules |
|---|---|---|
| `name` | yes | non-empty, ≤200 characters |
| `status` | yes | `active`, `paused` or `ended` |
| `daily_budget_cents` | yes | whole, non-negative integer (cents; no `$`, commas or decimals) |
| `objective` | yes | `awareness`, `consideration`, `conversions` or `retention` |

Header order is free and extra columns are ignored. Download the canonical header:

```bash
curl -sS "$BASE_URL/v1/bulk/campaigns/template.csv" -H "X-API-Key: $API_KEY"
# name,status,daily_budget_cents,objective
```

Valid rows are created, invalid rows are reported with 1-based line numbers (header is line 1)
and field names. HTTP 200 = all accepted; 207 = partial success; 422 = malformed header, empty
file, non-UTF-8 or oversized upload. See `examples/sample_campaigns.csv` (4 good, 2 bad) and
`examples/bad_campaigns.csv`.

## 10. Go-live checklist

- [ ] Credentials stored in a secret manager; never in git or client-side code.
- [ ] Auth mode chosen deliberately; OAuth scopes limited to what the integration actually uses.
- [ ] `GET /v1/partners/me` succeeds and the reported tier/rate limit match the contract.
- [ ] Base URL and credentials differ per environment; no production keys in staging.
- [ ] Pagination implemented by following `next_cursor` to `has_more == false` (no offset math).
- [ ] Retries limited to 429/502/503/504 + connection errors, honoring `Retry-After`, with jitter.
- [ ] All POST creates send a stable `Idempotency-Key`; PATCH sends `If-Match`.
- [ ] 401/403/404/422 are treated as terminal and surfaced with `request_id`.
- [ ] CSV exports are UTF-8, header matches the template, and 207 row errors surface in your UI.
- [ ] Rate-limit headers are logged so capacity headroom is visible.
- [ ] Support runbook (`docs/runbook.md`) shared with the on-call partner team.
- [ ] Load test at expected peak concurrency; confirm 429s are handled, not retried in a tight loop.

---

## Real example runs

All output below was captured from this repository against the local server with the seeded
dataset (`BASE_URL=http://127.0.0.1:8010`, API port overridden locally). Commands:

```bash
python examples/python/01_auth_api_key.py
python examples/python/02_oauth_token.py
python examples/python/03_paginate_all_campaigns.py
python examples/python/04_bulk_csv_import.py
python examples/python/05_handle_rate_limits.py
cd sdks/typescript && BASE_URL=http://127.0.0.1:8010 npm run example
```

### 01 - API key authentication

```text
Authenticated via api_key as Acme Retail (partner 1)
tier=free steady=60 rpm burst=120
scopes: campaigns:read, partner:read, reports:read
attempts=1 request_id=760be8788ff34a2aaabb49a984495924
```

### 02 - OAuth client credentials

```text
token_type=bearer expires_in=3600s scope=campaigns:read reports:read
access_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpX...
SDK reports auth_mode=oauth partner=Initech Global tier=enterprise
request_id=817be3e32dc64a019a979ad890820b2d
```

### 03 - Cursor pagination

```text
01 id=16  status=active Acme Retention Wave 16
02 id=7   status=paused Acme Conversion Blitz 07
03 id=10  status=active Acme Consideration Push 10
04 id=1   status=active Acme Brand Awareness 01
05 id=4   status=active Acme Retention Wave 04
06 id=13  status=ended  Acme Brand Awareness 13
07 id=19  status=active Acme Conversion Blitz 19
08 id=25  status=active Acme Brand Awareness 25
09 id=22  status=paused Acme Consideration Push 22
walked 9 campaigns (6 active) across pages of 4
```

### 04 - CSV bulk import (partial success)

```text
uploaded sample_campaigns.csv
accepted=4 rejected=2
  row 4 field=status: status must be active, paused or ended
  row 6 field=daily_budget_cents: daily_budget_cents must be a non-negative integer
created_ids: [40, 41, 42, 43]
attempts=1 request_id=43cbf09dcb8947e4be625ef189ea719c
```

### 05 - Rate-limit handling (fresh full bucket)

```text
fired 135 requests against a 60 rpm tier
200 responses: 120   429 responses: 15
retry timeline (429s):
  #121   775.9 ms -> 429 retry_after=1.0s request_id=ba33489645ef4e12831cdba99d4a3f7a
  #123   776.6 ms -> 429 retry_after=1.0s request_id=22d3e82a622043748cfbb1ce15a23765
  #124   778.2 ms -> 429 retry_after=1.0s request_id=c885b5ddaecb456692687b0032e2f1e7
  #122   779.0 ms -> 429 retry_after=1.0s request_id=2a9c7ab36ffb49ae905741557fe58e03
  #125   787.8 ms -> 429 retry_after=1.0s request_id=401ca38a0dca45888dc3dbc4ac6cbb53
  #126   790.8 ms -> 429 retry_after=1.0s request_id=8a575b4610fd4d068b2fd308828db26f
  ... 9 more 429s
waiting 1.0s for one token, then retrying with backoff enabled
recovered: Acme Retail after 1 HTTP attempt(s)
```

Re-running immediately drains the bucket; run `docker exec apipartnerhub-redis redis-cli DEL
ratelimit:free:1` (local demo only) or wait two minutes for a full burst.

### TypeScript quickstart

```text
Authenticated as Initech Global (enterprise, 3000 rpm, api_key)
  39 active Initech Retention Wave 04
  38 paused Globex Consideration Push 03
  37 active Acme Retargeting 02
  36 active Acme Spring Awareness 01
  35 active TS Quickstart Campaign
Walked 13 campaigns with automatic cursor pagination
createCampaign id=35 attempts=1
metrics rows=6 first={"date":"2026-11-16","impressions":187746,"clicks":3701,"spend_cents":348849,"conversions":198,"ctr_pct":1.9713,"cpc_cents":94.258,"cpm_cents":1858.0902,"cvr_pct":5.3499}
bulk accepted=4 rejected=2 errors=2
```
