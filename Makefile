SHELL := /bin/bash
PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
API_PORT ?= 8000
HOST ?= 127.0.0.1
UVICORN_WORKERS ?= 4

-include .env
export

.PHONY: install up down seed run health lint format typecheck test ci openapi openapi-check \
	sdk-install sdk-test bench examples clean

install:
	python3.11 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

up:
	docker compose up -d --wait

down:
	docker compose down

seed:
	$(PY) -m scripts.seed

run:
	$(PY) -m uvicorn app.main:app --host $(HOST) --port $(API_PORT) --reload

health:
	curl -sS "http://localhost:$(API_PORT)/healthz"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy app

test:
	$(PY) -m pytest --cov=app --cov-report=term-missing

ci: lint typecheck test

openapi:
	$(PY) -m scripts.export_openapi

openapi-check:
	$(PY) -m scripts.export_openapi --check

sdk-install:
	$(PIP) install -e sdks/python
	cd sdks/typescript && npm ci && npm run build

sdk-test:
	$(PY) -m pytest tests/test_sdk_python.py
	cd sdks/typescript && npm test

bench:
	$(PY) -m bench.run_bench

examples:
	$(PY) examples/python/01_auth_api_key.py
	$(PY) examples/python/02_oauth_token.py
	$(PY) examples/python/03_paginate_all_campaigns.py
	$(PY) examples/python/04_bulk_csv_import.py
	$(PY) examples/python/05_handle_rate_limits.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
