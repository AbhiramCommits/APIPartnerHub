# Changelog

All notable changes to APIPartnerHub are recorded here. This project uses a single `1.0.0`
line; the entries map to the build milestones.

## 1.0.0 — 2026-11-22

### Added

- FastAPI application scaffold with Pydantic v2 settings, SQLAlchemy 2 models for partners,
  API keys, OAuth clients, campaigns, daily metrics and idempotency records.
- Partner authentication via long-lived `apph_live_<prefix>_<secret>` API keys (Argon2 hashes)
  and OAuth 2.0 client-credentials JWTs with scopes, Redis `jti` revocation records and
  RFC-shaped token errors.
- Campaign CRUD with opaque base64 keyset cursors (limit ≤100), filters, `Idempotency-Key`
  replay/conflict semantics and ETag/`If-Match` optimistic updates.
- Metrics reporting with `day`/`campaign` grouping, server-computed CTR/CPC/CPM/CVR, JSON and
  streamed CSV output.
- CSV bulk import with per-row validation, 207 partial success, template download and row-level
  error details.
- Redis Lua token-bucket rate limiter (steady RPM, 2× burst), fail-open on Redis outage,
  `RateLimit-Limit/Remaining/Reset` headers and envelope-shaped 429s with `Retry-After`.
- Request-context middleware, structured JSON access logs, `X-Request-ID` propagation,
  in-process status/latency counters at `/v1/_internal/stats`.
- Error taxonomy, support runbook and curl cookbook.
- Python async SDK (cursor-following, automatic token refresh, idempotent creates, typed
  errors, retry/backoff honouring `Retry-After`) and TypeScript SDK with the same surface.
- Five Python examples and one TypeScript quickstart, all executed against the local server;
  their real output is captured in the integration guide.
- Committed OpenAPI JSON/YAML artifacts with export/validation scripts.
- Benchmark harness measuring latency/RPS/percentiles, rate-limit accuracy, SDK retry recovery,
  CSV throughput and auth overhead, with a committed `RESULTS.md`, CSV and p95 chart.
- 106 pytest tests (97.10% coverage of `app/`), 9 vitest tests, GitHub Actions CI for Python
  3.11/3.12, Node 20 and OpenAPI drift.

### Known limitations

No webhooks, single region, in-process metrics only, synchronous SQLAlchemy, client-credentials
OAuth only, no idempotency-record TTL, synchronous CSV import capped at 5 MB, and no
admin/portal API. See the README for the full list and next steps.
