# Error taxonomy

Every failure on the partner API (everything except `/oauth/token`, which follows RFC 6749)
uses one envelope:

```json
{
  "error": {
    "type": "rate_limited",
    "message": "Rate limit exceeded. Retry after the number of seconds in Retry-After.",
    "code": "RL-001",
    "doc_url": "https://docs.apipartnerhub.dev/errors#rate_limited",
    "request_id": "d2bc8a5089284d17ad49e2c5b1a899d9",
    "details": [{"field": "rate_limit", "message": "..."}]
  }
}
```

- `type` is a stable machine identifier (safe to branch on).
- `code` is the support code quoted in tickets.
- `request_id` is also echoed in the `X-Request-ID` response header and in every access log line.
  An inbound `X-Request-ID` is preserved (8–128 chars of `A-Za-z0-9._-`), otherwise one is generated.
- `details` is always present; it is `[]` when there is nothing field-specific to add.

## API errors

| type | HTTP | code | Cause | Partner-facing fix | Retryable? |
|---|---|---|---|---|---|
| `authentication_failed` | 401 | `AUTH-001` | Missing credentials, malformed API key, wrong secret, revoked key, expired or invalid JWT. | Send `X-API-Key: apph_live_<prefix>_<secret>` or a fresh `Authorization: Bearer <jwt>` from `/oauth/token`. Confirm the key was not revoked. | No — fix credentials first, then retry. |
| `insufficient_scope` | 403 | `SCOPE-001` | Credential is valid but lacks a required scope (e.g. `campaigns:write` or `bulk:write`). | Request a token with the required scope, or ask your account manager to grant it. The `details` array lists each missing scope. | No. |
| `not_found` | 404 | `NF-001` | Resource id does not exist **or belongs to another partner**. The API deliberately does not distinguish the two. | Verify the id came from your own list call. Never construct campaign ids manually. | No. |
| `conflict` | 409 | `CONFLICT-001` | `If-Match` ETag is stale, or an `Idempotency-Key` was reused with a different body. | Re-GET the campaign and retry with the fresh ETag. Reuse an idempotency key only with the exact original body. | Yes for ETag conflict (after re-read); no for idempotency body mismatch. |
| `validation_failed` | 422 | `VAL-001` | Bad query/path/body values, malformed cursor, unknown metric, malformed CSV header, null in PATCH. | Read `details[].field` and `details[].message`. For CSV header failures, download `/v1/bulk/campaigns/template.csv`. | No — fix the request. |
| `rate_limited` | 429 | `RL-001` | Per-partner token bucket exhausted (steady limit = tier RPM, burst = 2× RPM). | Back off for `Retry-After` seconds. Read `RateLimit-Remaining` and `RateLimit-Reset` to pace the next calls. | **Yes** — retry after the delay, preferably with jitter. |
| `internal_error` | 500 | `INTERNAL-001` | Unexpected server fault. | Capture `request_id` and contact partner support. | Yes with exponential backoff (max 3–5 attempts). |

## Rate-limit response headers

Present on **every** `/v1/*` response, success or failure:

| header | meaning |
|---|---|
| `RateLimit-Limit` | Burst capacity of the bucket (2 × the tier's steady requests per minute). |
| `RateLimit-Remaining` | Whole tokens left after this request. |
| `RateLimit-Reset` | Seconds until the bucket refills to full. |
| `Retry-After` | On 429 only: seconds until at least one token is available (minimum 1). |

Tier steady rates: free 60 rpm (burst 120), standard 600 rpm (burst 1200), enterprise 3000 rpm
(burst 6000). If Redis is unavailable the limiter **fails open** (requests are allowed, a
WARNING is logged, and the headers report the full default bucket).

## OAuth token endpoint (RFC 6749 shape)

`POST /oauth/token` errors are deliberately RFC-shaped, not the envelope, because standard
OAuth client libraries expect them:

| error | HTTP | Cause | Fix |
|---|---|---|---|
| `invalid_request` | 400 | `client_id` or `client_secret` missing. | Send both form fields. |
| `unsupported_grant_type` | 400 | Grant is not `client_credentials`. | Use `grant_type=client_credentials`. |
| `invalid_scope` | 400 | Requested scope is unknown or not granted to this client. | Omit `scope` to receive all granted scopes, or request a subset. |
| `invalid_client` | 401 | Unknown `client_id` or wrong secret. | Rotate/verify the client secret; response carries `WWW-Authenticate: Basic`. |

```json
{"error": "invalid_client", "error_description": "Client authentication failed."}
```

## HTTP 207 is not an error

`POST /v1/bulk/campaigns` returns **207 Multi-Status** when some rows were rejected but others
were created. The body is the bulk result (`accepted`, `rejected`, `errors`, `created_ids`),
not the error envelope. A malformed header, empty file, oversized upload or non-UTF-8 file is a
422 and does use the envelope.
