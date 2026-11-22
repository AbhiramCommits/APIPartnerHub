# Support runbook

This runbook is written for the Solutions Engineer on call. Every step is reproducible against
the local stack and maps to a production action.

## Log field contract

The API writes one JSON object per request to stdout (logger `apipartnerhub.access`):

```json
{"ts":"2026-11-22T06:56:05.534Z","request_id":"a952c2a75e7d4d3ab57cdc4053995ea4","partner_id":1,"method":"GET","path":"/v1/partners/me","status":429,"latency_ms":60.99,"rate_limit_remaining":0,"auth_mode":"api_key"}
```

Start a run that keeps a queryable log file:

```bash
make run 2>&1 | tee /tmp/apipartnerhub-api.log
```

All diagnostic commands below assume that file. Application warnings (Redis fail-open,
health-check failures, unhandled exceptions) are JSON too, with `level` and `message`.

---

## 1. Partner reports a 429 storm

**Confirm it, per partner**

```bash
jq -c 'select(.status == 429) | {ts, partner_id, request_id, path, rate_limit_remaining}' \
  /tmp/apipartnerhub-api.log | tail -20
```

Count 429s by partner in the last run:

```bash
jq -r 'select(.status == 429) | .partner_id' /tmp/apipartnerhub-api.log \
  | sort | uniq -c | sort -rn
```

**Check the live bucket and tier**

```bash
curl -sS "$BASE_URL/v1/partners/me" -H "X-API-Key: $PARTNER_API_KEY" \
  | jq '{tier, rate_limit_rpm, rate_limit_burst}'

curl -sS "$BASE_URL/v1/_internal/stats" -H "X-API-Key: $OPS_API_KEY" \
  | jq '{rate_limited_total, statuses: .requests_by_status_class}'

docker exec apipartnerhub-redis redis-cli \
  HGETALL "ratelimit:<tier>:<partner_id>"
# tokens (fractional) and ts (last refill, epoch ms)
```

**Root-cause checklist**

- Is client concurrency above the steady RPM for a sustained period? The bucket holds only 2×
  RPM; a 100-request cold-start burst on the free tier is expected to 429 after 120.
- Is the client ignoring `Retry-After` and retrying immediately? Look for retries < 1 s apart in
  the access log for the same `partner_id`/`path`.
- Does the account's tier match the contract? `rate_limit_rpm` is per partner row; a tier change
  requires updating that row and the docs sent to the partner.
- Are multiple integrations sharing one API key? The bucket is per **partner**, not per key, so
  separate teams drawing on the same credential exhaust one bucket.
- Did Redis just restart? Expect one `rate_limiter_redis_unavailable_fail_open` warning per
  request while it is down; during that window traffic is allowed, not throttled.

**Resolution**

1. Tell the partner the current limit and burst from `/v1/partners/me` (do not guess).
2. Have them honor `Retry-After` with exponential backoff and full jitter — the SDKs do this by
   default. See `docs/integration-guide.md`.
3. If sustained throughput is genuinely above contract, upgrade the tier / raise
   `rate_limit_rpm`; never raise it silently to clear an alert.
4. Verify recovery: 429 count stops increasing and `RateLimit-Remaining` returns above 0.

---

## 2. Partner reports 401 after key rotation

**Find the failing request**

```bash
jq -c 'select(.status == 401) | {ts, partner_id, request_id, path, auth_mode}' \
  /tmp/apipartnerhub-api.log | tail -20
```

Ask the partner for the `X-Request-ID` from the response header (or the `error.request_id` in
the body). Then:

```bash
jq -c --arg rid "$REQUEST_ID" 'select(.request_id == $rid)' /tmp/apipartnerhub-api.log
```

**Root-cause checklist**

- `auth_mode` is `null` in the access log → the request never authenticated. Common causes:
  header named `Authorization` instead of `X-API-Key`; missing `Bearer ` prefix; a proxy
  stripping the header.
- `authentication_failed` + "revoked" message → the old key still being used. Check the row:

  ```bash
  docker exec apipartnerhub-postgres psql -U apph -d apph -c \
    "SELECT prefix, revoked_at, last_used_at FROM api_keys WHERE prefix = '<prefix>';"
  ```

  The prefix is the middle segment of `apph_live_<prefix>_<secret>`.
- `last_used_at` updating every minute for the same prefix → rotation did not actually deploy;
  the partner is still on the old credential.
- OAuth path: expired JWT → request a new token; `invalid_client` at `/oauth/token` → wrong
  `client_secret`; a valid JWT rejected as revoked → the `jti` is not in Redis
  (`EXISTS oauth:jti:<jti>`), which happens after a Redis flush. Issue a new token.
- Clock skew: verify the token's `exp` with `jwt.decode(..., options={"verify_signature": false})`
  on the partner side; servers must be NTP-synced.

**Resolution**

1. Confirm which credential the partner sent by matching the key prefix against `api_keys`.
2. If the old key is revoked, issue a new one out of band and have the partner deploy it before
   revoking the next one (zero-downtime rotation).
3. Reproduce with curl before closing:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' "$BASE_URL/v1/partners/me" \
     -H "X-API-Key: $NEW_KEY"    # expect 200
   ```

4. Do not log or paste the full key or secret; only the prefix is safe.

---

## 3. CSV import rejects every row

**Distinguish the failure mode**

```bash
jq -c 'select(.path == "/v1/bulk/campaigns") | {status, partner_id, request_id, latency_ms}' \
  /tmp/apipartnerhub-api.log | tail -10
```

- **422** → the file itself was rejected before row processing: missing/renamed header, empty
  file, >5 MB, or not UTF-8. Read `error.details[0].message`.
- **207 with `accepted: 0`** → the header was fine but every row failed validation. Read
  `errors[]`: each entry has the 1-based CSV line number (`row`, header is line 1), the `field`
  and a message.
- **200** → everything was accepted; the partner is looking at a different environment.

**Get the exact header**

```bash
curl -sS "$BASE_URL/v1/bulk/campaigns/template.csv" -H "X-API-Key: $PARTNER_API_KEY"
# name,status,daily_budget_cents,objective
```

**Root-cause checklist**

- Header is case-sensitive and must contain exactly those four columns (extra columns are
  ignored, order is free, BOM is stripped).
- `status` ∈ `active|paused|ended`; `objective` ∈ `awareness|consideration|conversions|retention`.
- `daily_budget_cents` must be a whole, non-negative integer — no `$`, no decimals, no thousands
  separators.
- `name` non-empty and ≤200 chars.
- Rows are line-numbered including the header: an error on `row: 4` is the fourth line in the
  file.

**Resolution**

1. Send the partner the template and the field rules above.
2. Ask them to re-export as UTF-8 CSV.
3. Re-run their file against the local server with `examples/sample_campaigns.csv` as the known
   good reference (4 accepted, 2 rejected → 207).
4. Confirm `created_ids` are visible to the partner with `GET /v1/campaigns`.

---

## 4. Latency spike

**Localize it**

```bash
curl -sS "$BASE_URL/v1/_internal/stats" -H "X-API-Key: $OPS_API_KEY" \
  | jq '{latency_ms, endpoints}'
```

The endpoint map shows count and p50/p95/p99 per method+template. Compare the slow endpoint to
the overall number.

**Check dependencies**

```bash
curl -sS -w '\n%{http_code}\n' "$BASE_URL/healthz"
# 200 + {"db":"ok","redis":"ok"} is healthy

docker stats --no-stream apipartnerhub-postgres apipartnerhub-redis
docker exec apipartnerhub-postgres psql -U apph -d apph -c \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

**Root-cause checklist**

- 5xx spike? `jq -c 'select(.status >= 500)' /tmp/apipartnerhub-api.log` and correlate
  `request_id`. An unhandled exception is logged with its traceback.
- Redis down? `rate_limiter_redis_unavailable_fail_open` warnings + `/healthz` `redis: error`.
  The limiter fails open, so latency should not be caused by it, but auth JWT revocation checks
  also degrade.
- Postgres connection pressure? Uvicorn runs multiple workers; each process owns a pool. Check
  `pg_stat_activity` for many `idle in transaction` sessions and slow queries.
- Heavy report window? `GET /v1/reports/metrics` over 366 days across all campaigns is the
  expensive read; ask the partner to narrow the date range, use `group_by=day`, or page.
- CPU saturation from Argon2 API-key verification under a retry storm? A 429 storm means clients
  should be backing off; check the 429 count and fix the client.
- Sudden 200 → 500 transition on one endpoint only: check the access log for the first failing
  `request_id` and read the application error JSON at the same timestamp.

**Resolution**

1. Record p50/p95/p99 before and after any change from `/v1/_internal/stats`.
2. If a single worker is hot, restart/scale workers (`UVICORN_WORKERS`).
3. If Postgres is the bottleneck, add the missing index/filter or narrow the query; never raise
   timeouts to hide the problem.
4. Re-run the benchmark to confirm the fix:

   ```bash
   make bench
   ```

   and compare `bench/results/RESULTS.md`.

---

## Escalation packet

When handing off, include: partner id, `request_id`, UTC timestamp, endpoint, status, the
matching access-log line, the error envelope, and what was already ruled out from the
checklists above. Never include API keys, client secrets, or JWTs.
