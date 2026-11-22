# APIPartnerHub benchmark results

All figures below are measured by `bench/run_bench.py` against the real Postgres 16 + 
Redis 7 stack with the seeded dataset. The harness starts a 4-worker uvicorn, warms up 
each endpoint, then records every response it observed. `bench/analyze.py` only reads 
the raw JSON written by that run.

## Environment

- Machine: **Apple M3** (arm64), 8 logical CPUs
- OS: `macOS-27.2.1-arm64-arm-64bit`
- Python **3.11.14**, PostgreSQL **16.16 (Debian 16.16-1.pgdg13+1)**, Redis **7.4.12**
- uvicorn **4 workers**, load-generator concurrency **20**, **8.0s** per endpoint
- Seed: **25 campaigns**, **2250 daily metric rows**
- Base URL: `http://127.0.0.1:8011`
- Run timestamp (UTC): `2026-11-22T07:10:08Z`

## A. Latency and throughput

Enterprise tier (3000 rpm / 6000 burst) so the limiter is not the bottleneck. 
Each endpoint ran for the stated duration after a warm-up.

| Endpoint | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Error % |
|---|---:|---:|---:|---:|---:|---:|---:|
| `GET /v1/campaigns` | 719 | 88.56 | 125.73 | 743.27 | 1,226.26 | 1,712.17 | 0.00 |
| `GET /v1/campaigns/6` | 1,112 | 137.81 | 85.05 | 450.18 | 691.09 | 1,110.22 | 0.00 |
| `GET /v1/reports/metrics` | 1,238 | 153.29 | 72.61 | 421.27 | 617.90 | 950.94 | 0.00 |
| `POST /v1/campaigns` | 1,146 | 142.54 | 69.61 | 469.59 | 743.71 | 1,810.39 | 0.00 |

Error % counts responses outside the endpoint's expected status (or transport exceptions) divided by requests.

## B. Rate-limit enforcement accuracy

Fired **140** simultaneous requests at the free-tier key (steady **60 rpm**, burst capacity **120**).

| Metric | Value |
|---|---:|
| Configured burst capacity | 120 |
| Allowed (200) | 120 |
| Rate limited (429) | 20 |
| Other failures | 0 |
| Observation window | 1.048 s |
| Tokens refilled during window (theoretical) | 1.05 |
| Absolute deviation (allowed − capacity) | +0 |
| Deviation | +0.00% |
| 429 responses with valid Retry-After | 20/20 |
| Retry-After values observed (s) | [1] |
| 200 responses with all RateLimit headers | 120/120 |
| 429 bodies using the `rate_limited` envelope | 20/20 |

The token bucket is refilled continuously, so a fast burst can legitimately allow the theoretical refill shown above on top of the initial capacity.

## C. SDK retry recovery

The free-tier bucket was drained, then the same Python SDK was driven with retries **off** and **on** for 5 sequential requests each.

| Metric | Without retries | With retries |
|---|---:|---:|
| Successful responses | 0/5 (0.00%) | 5/5 (100.00%) |
| Mean request latency | 6.35 ms | 1,022.55 ms |
| Total retries performed | — | 5 |
| Baseline mean latency (fresh bucket) | 6.13 ms | — |
| Mean added latency vs baseline | — | 1,016.42 ms |

Exhaustion phase allowed 120 requests before returning 429s; retries slept for the server-provided Retry-After.

## D. CSV bulk import throughput

| Rows | Seconds | Rows/sec | Accepted | Rejected | HTTP |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.159 | 6,277.46 | 980 | 20 | 207 |
| 10,000 | 1.184 | 8,445.10 | 9,800 | 200 | 207 |

Each file deliberately contains 2% invalid rows (every 50th row has status `launched`) so the import exercises per-row validation and the 207 partial-success path.

## E. Auth-mode overhead

Same endpoint (`GET /v1/partners/me`), enterprise tier, sequential requests.

| Auth mode | Requests | Mean (ms) | p50 (ms) | p95 (ms) | Errors |
|---|---:|---:|---:|---:|---:|
| API key | 30 | 4.16 | 3.99 | 5.88 | 0 |
| OAuth bearer | 30 | 5.34 | 4.59 | 9.76 | 0 |

API-key auth is **1.18 ms faster** per request on mean. Successful API-key verification is cached in-process for 30 s (the revocation flag is still read from Postgres on every request); OAuth verifies a JWT signature and performs a Redis `jti` lookup.

## Reproduce

```bash
docker compose up -d --wait
python -m scripts.seed
python -m bench.run_bench --port 8011 --workers 4 --concurrency 20 --duration 8
python -m bench.analyze
# or simply: make bench
```

Raw observations: `bench/results/raw_environment.json`, `raw_latency.json`, 
`raw_rate_limit.json`, `raw_retry.json`, `raw_csv.json`, `raw_auth.json`. 
Per-endpoint CSV: `bench/results/latency_by_endpoint.csv`. 
Chart: `bench/results/latency_p95.png`.

