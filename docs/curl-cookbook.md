# curl cookbook

Every command below was executed against the local stack
(`docker compose up -d`, `python -m scripts.seed`, `uvicorn app.main:app`). Set the base URL
and credentials first — `python -m scripts.seed` prints them once and writes
`.seed_credentials.json` locally (gitignored).

```bash
export BASE_URL=http://localhost:8000     # set API_PORT if you override the port
export API_KEY='apph_live_...'            # free-tier partner
export API_KEY_WRITE='apph_live_...'      # standard/enterprise partner (campaigns:write, bulk:write)
export CLIENT_ID='apc_...'
export CLIENT_SECRET='...'
```

## Health and docs

```bash
curl -sS "$BASE_URL/healthz"
# {"status":"ok","version":"1.0.0","env":"development","db":"ok","redis":"ok"}

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE_URL/docs"
# 200

curl -sS "$BASE_URL/openapi.json" | jq '.info | {title, version}'
```

## Partner profile

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

## OAuth 2.0 client credentials

```bash
TOKEN=$(curl -sS -X POST "$BASE_URL/oauth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id="$CLIENT_ID" \
  --data-urlencode client_secret="$CLIENT_SECRET" \
  --data-urlencode 'scope=campaigns:read reports:read' \
  | jq -r .access_token)

curl -sS "$BASE_URL/v1/partners/me" -H "Authorization: Bearer $TOKEN" | jq '{tier, auth_mode, scopes}'
# {"tier":"enterprise","auth_mode":"oauth","scopes":["campaigns:read","reports:read"]}
```

Wrong secret returns the RFC 6749 error shape (not the API envelope):

```bash
curl -sS -o /tmp/bad.json -w '%{http_code}\n' -X POST "$BASE_URL/oauth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials&client_id=wrong&client_secret=wrong'
cat /tmp/bad.json
# {"error":"invalid_client","error_description":"Client authentication failed."}
```

## List campaigns with cursor pagination

```bash
curl -sS "$BASE_URL/v1/campaigns?limit=3" -H "X-API-Key: $API_KEY" > /tmp/page1.json
jq '{names: [.data[].name], next_cursor, has_more}' /tmp/page1.json

CURSOR=$(jq -r .next_cursor /tmp/page1.json)
curl -sS "$BASE_URL/v1/campaigns?limit=100&cursor=$CURSOR" \
  -H "X-API-Key: $API_KEY" | jq '{count: (.data|length), has_more, next_cursor}'
# {"count":6,"has_more":false,"next_cursor":null}
```

```json
{
  "names": [
    "Acme Retention Wave 16",
    "Acme Conversion Blitz 07",
    "Acme Consideration Push 10"
  ],
  "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wOS0yOVQwNjozODozMy44NDgwODErMDA6MDAiLCJpZCI6MTB9",
  "has_more": true
}
```

Filters and an intentionally broken cursor:

```bash
curl -sS "$BASE_URL/v1/campaigns?status=active&objective=conversions&name_contains=Acme" \
  -H "X-API-Key: $API_KEY" | jq '[.data[] | {id, name, status}]'

curl -sS -o /tmp/badcursor.json -w '%{http_code}\n' \
  "$BASE_URL/v1/campaigns?cursor=not-a-cursor" -H "X-API-Key: $API_KEY"
# 422
jq '.error | {type, code, details}' /tmp/badcursor.json
```

## Create with idempotency

```bash
IDEM="curl-demo-$(date +%s)"
BODY='{"name":"Curl Demo Campaign","status":"active","daily_budget_cents":12345,"objective":"consideration"}'

# first call -> 201
curl -sS -D /tmp/h1.txt -o /tmp/c1.json -w '%{http_code}\n' \
  -X POST "$BASE_URL/v1/campaigns" \
  -H "X-API-Key: $API_KEY_WRITE" -H "Idempotency-Key: $IDEM" \
  -H 'Content-Type: application/json' -d "$BODY"

# replay -> 201 with Idempotency-Replayed: true and the identical body
curl -sS -D /tmp/h2.txt -o /tmp/c2.json -w '%{http_code}\n' \
  -X POST "$BASE_URL/v1/campaigns" \
  -H "X-API-Key: $API_KEY_WRITE" -H "Idempotency-Key: $IDEM" \
  -H 'Content-Type: application/json' -d "$BODY"
grep -i idempotency-replayed /tmp/h2.txt       # idempotency-replayed: true
diff <(jq -S . /tmp/c1.json) <(jq -S . /tmp/c2.json) && echo 'bodies identical'

# same key, different body -> 409
curl -sS -o /tmp/c3.json -w '%{http_code}\n' -X POST "$BASE_URL/v1/campaigns" \
  -H "X-API-Key: $API_KEY_WRITE" -H "Idempotency-Key: $IDEM" \
  -H 'Content-Type: application/json' -d '{"name":"Different Body","daily_budget_cents":1}'
```

```json
{
  "id": 26,
  "name": "Curl Demo Campaign",
  "status": "active",
  "daily_budget_cents": 12345,
  "objective": "consideration",
  "created_at": "2026-11-22T06:52:34.612254Z",
  "updated_at": "2026-11-22T06:52:34.612268Z"
}
```

## Get, ETag and conditional PATCH

```bash
ETAG=$(curl -sS -D - -o /dev/null "$BASE_URL/v1/campaigns/26" \
  -H "X-API-Key: $API_KEY_WRITE" | tr -d '\r' | awk -F': ' 'tolower($1)=="etag"{print $2}')

curl -sS -D /tmp/patch_headers.txt -o /tmp/patched.json -w '%{http_code}\n' \
  -X PATCH "$BASE_URL/v1/campaigns/26" \
  -H "X-API-Key: $API_KEY_WRITE" -H "If-Match: $ETAG" \
  -H 'Content-Type: application/json' \
  -d '{"status":"paused","daily_budget_cents":9999}'
# 200

# Reusing the stale ETag -> 409
curl -sS -o /tmp/stale.json -w '%{http_code}\n' \
  -X PATCH "$BASE_URL/v1/campaigns/26" \
  -H "X-API-Key: $API_KEY_WRITE" -H "If-Match: $ETAG" \
  -H 'Content-Type: application/json' -d '{"status":"active"}'
# 409
```

## Cross-partner isolation

Partner 1 does not own campaign 26 (it belongs to partner 2), so the response is the same
404 envelope as a truly missing campaign — existence is never leaked.

```bash
curl -sS -o /tmp/nf.json -w '%{http_code}\n' \
  "$BASE_URL/v1/campaigns/26" -H "X-API-Key: $API_KEY"
# 404
jq . /tmp/nf.json
```

```json
{
  "error": {
    "type": "not_found",
    "message": "Campaign 26 was not found.",
    "code": "NF-001",
    "doc_url": "https://docs.apipartnerhub.dev/errors#not_found",
    "request_id": "unknown",
    "details": []
  }
}
```

## Metrics report (JSON and CSV)

```bash
curl -sS "$BASE_URL/v1/reports/metrics?start_date=2026-09-01&end_date=2026-09-05&group_by=day" \
  -H "X-API-Key: $API_KEY" | jq '{row_count, first: .data[0]}'

curl -sS -D /tmp/csv_headers.txt -o /tmp/metrics.csv \
  "$BASE_URL/v1/reports/metrics?start_date=2026-09-01&end_date=2026-09-03&group_by=day&format=csv" \
  -H "X-API-Key: $API_KEY"
cat /tmp/metrics.csv

curl -sS "$BASE_URL/v1/reports/metrics?start_date=2026-09-01&end_date=2026-11-21&group_by=campaign&metrics=impressions,clicks" \
  -H "X-API-Key: $API_KEY" | jq '{row_count, first: .data[0]}'
```

```csv
date,impressions,clicks,spend_cents,conversions,ctr_pct,cpc_cents,cpm_cents,cvr_pct
2026-09-01,163966,3896,354907,237,2.3761,91.0952,2164.5158,6.0832
2026-09-02,180037,4078,387915,218,2.2651,95.1238,2154.6404,5.3458
2026-09-03,195704,3384,321714,158,1.7291,95.0691,1643.8806,4.669
```

## Bulk CSV import

```bash
# Template header
curl -sS "$BASE_URL/v1/bulk/campaigns/template.csv" -H "X-API-Key: $API_KEY_WRITE"
# name,status,daily_budget_cents,objective

# examples/sample_campaigns.csv has 4 valid and 2 invalid rows -> 207
curl -sS -o /tmp/bulk.json -w '%{http_code}\n' \
  -X POST "$BASE_URL/v1/bulk/campaigns" \
  -H "X-API-Key: $API_KEY_WRITE" \
  -F 'file=@examples/sample_campaigns.csv;type=text/csv'
jq . /tmp/bulk.json

# Malformed header -> 422 envelope
printf 'name,status\nFoo,active\n' > /tmp/badheader.csv
curl -sS -o /tmp/badheader.json -w '%{http_code}\n' \
  -X POST "$BASE_URL/v1/bulk/campaigns" \
  -H "X-API-Key: $API_KEY_WRITE" -F 'file=@/tmp/badheader.csv;type=text/csv'
```

```json
{
  "accepted": 4,
  "rejected": 2,
  "errors": [
    {"row": 4, "field": "status", "message": "status must be active, paused or ended"},
    {"row": 6, "field": "daily_budget_cents", "message": "daily_budget_cents must be a non-negative integer"}
  ],
  "created_ids": [27, 28, 29, 30]
}
```

## Error envelope shape

Every API error (everything except `/oauth/token`) has exactly this shape:

```json
{
  "error": {
    "type": "validation_failed",
    "message": "Invalid pagination cursor.",
    "code": "VAL-001",
    "doc_url": "https://docs.apipartnerhub.dev/errors#validation_failed",
    "request_id": "0b6b1f5d6bd24b7e8f6c0d6a2f1c9e77",
    "details": [{"field": "cursor", "message": "..."}]
  }
}
```
