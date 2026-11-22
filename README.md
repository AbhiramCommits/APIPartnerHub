# APIPartnerHub

[![CI workflow](https://img.shields.io/badge/CI-python_3.11_%7C_3.12_%2B_node_20-success?style=flat-square)](.github/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-106%20pytest%20%2B%209%20vitest-brightgreen)](tests/)
[![coverage](https://img.shields.io/badge/coverage-97.10%25-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-20-blue)](sdks/typescript/package.json)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

APIPartnerHub is a partner-facing advertising data API platform — the system a Solutions
Engineer maintains so external advertisers, agencies and integration partners can pull campaign
performance, push bulk changes, and build reliable integrations on top of it.

## What it is

The consumers are **external**: advertisers running their own analytics, agencies consolidating
many brands, and integration partners embedding campaign data in their products. They cannot be
trusted with internal infrastructure and they do not read your source code. That changes the
product requirements:

- **Authentication for outsiders** — long-lived API keys for simple server-to-server jobs, and
  OAuth 2.0 client-credentials JWTs with scopes when credentials must rotate or be limited.
- **Predictable rate limits** — a per-partner token bucket with burst capacity, `RateLimit-*`
  headers on every response and a machine-readable 429 with `Retry-After`.
- **Retryable, actionable errors** — one envelope with a stable `type`, support `code`,
  documentation URL, field/row `details` and a `request_id` that appears in the logs.
- **Safe writes** — cursor pagination and `Idempotency-Key` for creates, ETag/`If-Match` for
  updates, and strict tenant isolation on every query.
- **Spreadsheet workflows** — CSV bulk import with per-row validation (207 partial success) and
  streamed CSV report exports.
- **Client tooling** — Python and TypeScript SDKs with cursor-following, idempotent creates and
  retry/backoff that honours `Retry-After`, plus six runnable examples.
- **Supportability** — structured JSON access logs, request-id propagation, an in-process stats
  endpoint, an error taxonomy and a troubleshooting runbook.

## Features and endpoints

| Capability | Endpoint | Auth | Required scope |
|---|---|---|---|
| Health and dependency check | `GET /healthz` | none | — |
| OAuth client-credentials token | `POST /oauth/token` | client id + secret | — |
| Partner profile, tier, limits, scopes | `GET /v1/partners/me` | API key **or** OAuth | `partner:read` |
| Cursor-paginated campaigns + filters | `GET /v1/campaigns` | API key **or** OAuth | `campaigns:read` |
| Create campaign (idempotent) | `POST /v1/campaigns` | API key **or** OAuth | `campaigns:write` |
| Get one campaign | `GET /v1/campaigns/{id}` | API key **or** OAuth | `campaigns:read` |
| Partial update with ETag | `PATCH /v1/campaigns/{id}` | API key **or** OAuth | `campaigns:write` |
| Aggregated metrics (JSON or CSV stream) | `GET /v1/reports/metrics` | API key **or** OAuth | `reports:read` |
| CSV campaign import (207 partial) | `POST /v1/bulk/campaigns` | API key **or** OAuth | `bulk:write` |
| CSV template download | `GET /v1/bulk/campaigns/template.csv` | API key **or** OAuth | `bulk:write` |
| Request counters and latency percentiles | `GET /v1/_internal/stats` | API key **or** OAuth | `partner:read` |

The API exposes **9 paths / 11 operations** (verified against the committed OpenAPI artifact).

## Architecture

```text
 Partner app / agency / integration partner
        │  X-API-Key: apph_live_…   OR   Authorization: Bearer <JWT>
        ▼
 Python SDK / TypeScript SDK
   cursor-following · Idempotency-Key · retry 429/502/503/504 with Retry-After + jitter
        │  HTTPS
        ▼
 FastAPI + uvicorn (N workers)
   middleware chain (outermost → innermost):
     X-Request-ID ─► access log (JSON) ─► rate limit ─► route auth/scope
                                             │  token bucket Lua
                                             ├─ 200: RateLimit-Limit/Remaining/Reset
                                             └─ 429: Retry-After + error envelope
        │
        ├── error envelope ── {type, message, code, doc_url, request_id, details}
        ├── idempotency store ── unique(partner_id, key, endpoint) + request-body hash
        └── observability ──── /v1/_internal/stats: counts, 429s, p50/p95/p99
        ▼
 Postgres 16                          Redis 7
   partners, api_keys, oauth_clients    rate-limit buckets (per partner + tier)
   campaigns, metrics_daily             OAuth jti revocation records
   idempotency_records
```

Middleware order is deliberate: `RequestContextMiddleware` is outermost so every log line and
every error body (including a rate-limit rejection) has an `X-Request-ID`; the access log wraps
the limiter so 429s are logged too; the limiter authenticates first and caches the identity for
the route dependency, so the limiter is partner-specific without a second credential lookup.

### Repository layout

```text
APIPartnerHub/
├── app/                 FastAPI app: config, models, auth, errors, middleware, routers
├── bench/               asyncio load harness + analyzer writing RESULTS.md/CSV/chart
├── bench/results/       committed measured results (raw JSON is gitignored)
├── docs/                error taxonomy, runbook, integration guide, curl cookbook
├── examples/            sample/bad CSVs and 5 Python + 1 TypeScript runnable examples
├── scripts/             seed, OpenAPI export and validation
├── sdks/python/         async Python SDK (httpx)
├── sdks/typescript/     TypeScript SDK (native fetch, tsc + vitest)
├── tests/               pytest suite on SQLite + fakeredis (no services needed)
├── openapi.json/.yaml   committed spec generated from the live app
├── docker-compose.yml   postgres:16 + redis:7 with named volumes and healthchecks
├── Makefile             install/up/seed/run/test/ci/openapi/bench targets
└── pyproject.toml       deps, ruff, mypy, pytest, coverage configuration
```

## How to run

Prerequisites: Docker, Python 3.11+, Node 20+ (for the TypeScript SDK/tests).

```bash
cp .env.example .env
make install                 # venv + project and dev dependencies
docker compose up -d --wait  # postgres:16 on 5432, redis:7 on 6379
make seed                    # 3 partners, 25 campaigns, 2250 metric rows, prints credentials
make run                     # uvicorn on :8000 with reload
```

`make bootstrap` runs `install`, `up` and `seed` in one step.

- API docs: <http://localhost:8000/docs> (Swagger UI) and `/redoc`
- Health: `curl localhost:8000/healthz`
- Credentials are printed once and written to `.seed_credentials.json` (gitignored).

If host ports 5432/6379/8000 are already taken (as on the machine these results were measured),
set `POSTGRES_PORT`, `REDIS_PORT` and `API_PORT` in `.env`; compose and the Makefile pick them
up. This repo's local run used 55433, 56380 and 8010.

### SDKs and examples

```bash
pip install -e sdks/python
cd sdks/typescript && npm ci && npm run build && cd ../..

BASE_URL=http://localhost:8000 python examples/python/01_auth_api_key.py
BASE_URL=http://localhost:8000 python examples/python/02_oauth_token.py
BASE_URL=http://localhost:8000 python examples/python/03_paginate_all_campaigns.py
BASE_URL=http://localhost:8000 python examples/python/04_bulk_csv_import.py
BASE_URL=http://localhost:8000 python examples/python/05_handle_rate_limits.py
cd sdks/typescript && BASE_URL=http://localhost:8000 npm run example
```

### Tests, spec and benchmarks

```bash
make test        # pytest with coverage floor
make ci          # ruff + format + mypy + pytest + OpenAPI check + vitest
make openapi     # regenerate openapi.json / openapi.yaml and validate
make bench       # reseed, start a 4-worker server, measure, write RESULTS.md
```

## Results

All figures below were produced by commands in this repository and are copied from
[`bench/results/RESULTS.md`](bench/results/RESULTS.md) and the pytest/coverage output. Nothing
is estimated.

### Environment

- Machine: **Apple M3**, arm64, **8 logical CPUs**, `macOS-27.2.1-arm64-arm-64bit`
- **Python 3.11.14**, **PostgreSQL 16.16**, **Redis 7.4.12**
- uvicorn **4 workers**, load-generator concurrency **20**, **8 s per endpoint**
- Dataset: **25 campaigns**, **2,250 daily metric rows** (deterministic seed 42)
- Base URL: `http://127.0.0.1:8011`, run at `2026-11-22T07:10:08Z`

Command: `make bench` (compose up → seed → 4-worker server → harness → analyzer).

### Latency and throughput (enterprise tier)

| Endpoint | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Error % |
|---|---:|---:|---:|---:|---:|---:|---:|
| `GET /v1/campaigns` | 719 | 88.56 | 125.73 | 743.27 | 1,226.26 | 1,712.17 | 0.00 |
| `GET /v1/campaigns/{id}` | 1,112 | 137.81 | 85.05 | 450.18 | 691.09 | 1,110.22 | 0.00 |
| `GET /v1/reports/metrics` | 1,238 | 153.29 | 72.61 | 421.27 | 617.90 | 950.94 | 0.00 |
| `POST /v1/campaigns` | 1,146 | 142.54 | 69.61 | 469.59 | 743.71 | 1,810.39 | 0.00 |

Headline: **153.29 RPS** peak endpoint throughput, **421–743 ms p95**, **0.00% errors** across
4,215 measured requests. Command: `python -m bench.run_bench --port 8011 --workers 4
--concurrency 20 --duration 8`; per-endpoint CSV in
[`bench/results/latency_by_endpoint.csv`](bench/results/latency_by_endpoint.csv).

### Rate-limit enforcement accuracy (free tier)

| Metric | Value |
|---|---:|
| Configured burst capacity (60 rpm × 2) | 120 |
| Requests fired simultaneously | 140 |
| Allowed (200) | 120 |
| Rate limited (429) | 20 |
| Observation window | 1.048 s |
| Absolute deviation (allowed − capacity) | +0 |
| Deviation | **+0.00%** |
| 429s with valid `Retry-After` | 20/20 |
| `Retry-After` values observed | `[1]` s |
| 200s carrying all `RateLimit-*` headers | 120/120 |
| 429 bodies using the `rate_limited` envelope | 20/20 |

Command: `python -m bench.run_bench` scenario B (included in `make bench`).

### SDK retry recovery (free tier)

| Metric | Without retries | With retries |
|---|---:|---:|
| Successful responses | 0/5 (0.00%) | **5/5 (100.00%)** |
| Mean request latency | 6.35 ms | 1,022.55 ms |
| Total retries performed | — | 5 |
| Baseline mean latency (fresh bucket) | 6.13 ms | — |
| Mean added latency vs baseline | — | 1,016.42 ms |

The bucket was drained before each phase. Retries sleep exactly the server-provided
`Retry-After` (1 s), so the added latency is the expected cost of recovery, not overhead.
Command: scenario C of `make bench`.

### CSV bulk import throughput (enterprise tier)

| Rows | Seconds | Rows/sec | Accepted | Rejected | HTTP |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.159 | 6,277.46 | 980 | 20 | 207 |
| 10,000 | 1.184 | **8,445.10** | 9,800 | 200 | 207 |

Both files intentionally contain 2% invalid rows to exercise row-level validation and the 207
partial-success path. Command: scenario D of `make bench`.

### API-key vs OAuth auth overhead

| Auth mode | Requests | Mean (ms) | p50 (ms) | p95 (ms) | Errors |
|---|---:|---:|---:|---:|---:|
| API key | 30 | 4.16 | 3.99 | 5.88 | 0 |
| OAuth bearer | 30 | 5.34 | 4.59 | 9.76 | 0 |

API-key auth is **1.18 ms faster on mean** in this build: successful API-key verification is
cached in-process for 30 s (the revocation flag is still read from Postgres on every request),
while OAuth verifies a JWT signature and performs a Redis `jti` lookup. Command: scenario E of
`make bench`.

### Test and spec counts

- **106 pytest tests passed** (API, tenant isolation, pagination, idempotency, rate limiting,
  fail-open, CSV, metric math, errors, SDK) on SQLite + fakeredis — no services required.
- **97.10% coverage** of `app/` (floor enforced at 85%).
- **9 vitest tests passed** for TypeScript retry/backoff and error mapping.
- **9 OpenAPI paths / 11 operations / 14 schemas**, validated by
  `python -m scripts.validate_openapi`; `make openapi-check` proves the artifact matches code.

```bash
make test        # 106 passed, 97.10% coverage, exit 0
cd sdks/typescript && npm test   # 9 passed
make openapi-check               # artifacts up to date + valid
```

These numbers are from the local `make ci` run recorded in this repository's development
session. Re-run the commands to reproduce them on another machine; latency and RPS will differ.

## Developer experience

- [Error taxonomy](docs/errors.md) — every type, HTTP status, code, cause, fix, retryability.
- [Support runbook](docs/runbook.md) — 429 storms, post-rotation 401s, CSV rejections, latency
  spikes, with exact log queries and endpoints.
- [Integration guide](docs/integration-guide.md) — onboarding, auth choice, pagination and
  idempotency contracts, per-tier limits, retry guidance, CSV fields, go-live checklist and the
  real console output of all six examples.
- [curl cookbook](docs/curl-cookbook.md) — working commands for every endpoint, including the
  207 partial import and cross-tenant 404.
- SDKs: [`sdks/python/README.md`](sdks/python/README.md),
  [`sdks/typescript/README.md`](sdks/typescript/README.md).

## Limitations and next steps

Honest constraints of this build:

- **No webhooks/event stream.** Partners poll lists/reports; a signed webhook delivery system
  with replay protection is the next integration feature.
- **Single region, single primary database.** No read replicas, failover rehearsal or
  multi-region routing.
- **In-process metrics.** `/v1/_internal/stats` is per worker and per process; a production
  deployment should export Prometheus/OpenTelemetry instead of scraping one worker.
- **Synchronous SQLAlchemy.** Handlers run in FastAPI's threadpool. It is predictable and the
  benchmark numbers reflect it, but an async database driver would raise the concurrency
  ceiling.
- **Rate limits are per partner, not per key or per endpoint.** Multiple integrations sharing a
  credential share one bucket; the burst is 2× the steady RPM.
- **OAuth is client-credentials only.** No authorization-code flow, refresh tokens or consent
  UI. JWT revocation relies on Redis `jti` records; if Redis is down, revocation checks fail
  open and a warning is logged.
- **Idempotency records have no TTL yet.** They are retained until an operator cleans them up;
  use a bounded key namespace.
- **CSV import is synchronous and capped at 5 MB.** Very large imports should move to an async
  job with a status endpoint.
- **No admin/portal API.** Partner, key and client provisioning is done by the seed script or
  out-of-band SQL; a portal API is future work.
- **SQLite is only a test substitute.** Tests cover behavior, not Postgres-specific DDL,
  timezone storage or query plans; the benchmark is the Postgres check.
- **No audit trail beyond `last_used_at`.** Key usage history is in structured logs, not a
  queryable audit table.

## License

MIT — see [LICENSE](LICENSE). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Changes:
[docs/CHANGELOG.md](docs/CHANGELOG.md).
