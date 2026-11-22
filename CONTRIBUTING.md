# Contributing

## Setup

```bash
cp .env.example .env
make install
docker compose up -d --wait
make seed
make run
```

Run the full gate before opening a pull request:

```bash
make ci
```

That runs `ruff check`, `ruff format --check`, `mypy app`, `pytest --cov=app
--cov-fail-under=85`, the OpenAPI freshness/validation checks and the TypeScript `vitest` suite.
No Postgres or Redis service is needed for tests: the suite uses SQLite plus fakeredis.

## Code conventions

- **Never use `HTTPException` for partner-facing failures.** Raise an `APIError` subclass from
  `app/errors.py` so the single envelope, stable code, doc URL and request id are preserved.
  Add a new subclass + code + taxonomy row if a genuinely new failure category appears.
- **Every campaign/metric/idempotency query goes through `app/queries.py`.** The `scoped_*`
  helpers enforce `partner_id`; do not build ad-hoc selects against those tables. A new endpoint
  must have a cross-tenant test proving 404/empty results for a non-owner.
- **Use `require_scopes(...)`.** It accepts API keys or OAuth bearer tokens and returns an
  `AuthContext`; do not read credentials from headers directly in a router.
- **Blocking work stays out of the event loop.** Route handlers and dependencies are synchronous
  so FastAPI runs them in the threadpool; middleware offloads auth + Redis with
  `run_in_threadpool`.
- **Money is integer cents; rates are percentages** (`ctr_pct`, `cvr_pct`) or cents
  (`cpc_cents`, `cpm_cents`). Keep the docs on the Pydantic fields in sync.
- New public response fields need descriptions and examples in `app/schemas.py`; the OpenAPI
  quality validator fails on missing descriptions.

## Adding an endpoint

1. Add request/response models to `app/schemas.py` with `Field(description=...)` and an example.
2. Implement the handler in the appropriate `app/routers/*.py` with
   `Depends(require_scopes("<scope>"))` and the tenant-scoped helper.
3. Include the router in `app/main.py` if it is a new module.
4. Add the operation's scope to `_required_scope()` in `app/main.py` if the path is new.
5. Write tests: happy path, validation, tenant isolation and any new error branch.
6. Regenerate and validate the spec:

   ```bash
   make openapi
   make openapi-check
   ```

7. Update `docs/integration-guide.md` (and `docs/curl-cookbook.md` for a curl example).
8. Run `make ci`. Commit `openapi.json`, `openapi.yaml` and the tests with the code.

## Changing the data model

`Base.metadata.create_all` is used for local development and tests; there is no migration tool
yet. If you change a model, update `scripts/seed.py`, re-seed locally, and note the migration
requirement in `docs/CHANGELOG.md`. Do not commit `.env`, `.seed_credentials.json`, `*.db` files
or `bench/results/raw_*.json`.
