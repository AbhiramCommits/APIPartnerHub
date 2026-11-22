"""Real load, rate-limit, retry, CSV and auth-overhead measurements.

Starts a multi-worker uvicorn against the compose Postgres + Redis stack, warms up, then
measures each scenario and writes raw JSON to ``bench/results/``. No figures are synthesised:
every value comes from a response observed in this run.

Usage:
    python -m bench.run_bench --port 8011 --workers 4 --concurrency 20 --duration 8
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from apipartnerhub import APIPartnerHubClient, APIPartnerHubError, RetryPolicy
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models import Campaign, MetricDaily
from app.redis_client import get_redis

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "bench" / "results"
CREDENTIALS = ROOT / ".seed_credentials.json"


@dataclass(slots=True)
class EndpointSpec:
    name: str
    method: str
    path: str
    expected: set[int]
    params: dict[str, Any] | None = None
    body_factory: Callable[[int], dict[str, Any]] | None = None


def load_credentials() -> dict[str, dict[str, Any]]:
    if not CREDENTIALS.exists():
        raise SystemExit("Missing .seed_credentials.json. Run `python -m scripts.seed` first.")
    payload = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
    return {entry["tier"]: entry for entry in payload["partners"]}


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * p
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def latency_stats(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "max_ms": round(max(values), 2) if values else 0.0,
        "mean_ms": round(statistics.fmean(values), 2) if values else 0.0,
    }


def write_json(name: str, data: Any) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / name
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def cpu_brand() -> str:
    if platform.system() == "Darwin":
        try:
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or platform.machine()


def collect_environment(
    *, port: int, workers: int, concurrency: int, duration: float
) -> dict[str, Any]:
    with SessionLocal() as session:
        pg_version = str(session.scalar(text("SHOW server_version")))
        campaigns = int(session.scalar(select(func.count()).select_from(Campaign)) or 0)
        metrics = int(session.scalar(select(func.count()).select_from(MetricDaily)) or 0)
    redis_info = get_redis().info("server")
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": cpu_brand(),
        "platform": platform.platform(),
        "machine_arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "postgres_version": pg_version,
        "redis_version": str(redis_info.get("redis_version", "unknown")),
        "uvicorn_workers": workers,
        "base_url": f"http://127.0.0.1:{port}",
        "concurrency": concurrency,
        "duration_seconds_per_endpoint": duration,
        "seeded_campaigns": campaigns,
        "seeded_metric_rows": metrics,
    }


def wait_for_health(base_url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Server at {base_url} did not become healthy")


def start_server(port: int, workers: int) -> tuple[subprocess.Popen[bytes], Any]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / "server.log"
    log = log_path.open("wb")
    env = os.environ.copy()
    env["LOG_LEVEL"] = "WARNING"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--no-access-log",
    ]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    return process, log


async def run_load(
    client: httpx.AsyncClient,
    spec: EndpointSpec,
    *,
    concurrency: int,
    duration: float,
) -> dict[str, Any]:
    counter = itertools.count()
    latencies: list[float] = []
    errors = 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    status_counts: dict[str, int] = {}

    async def worker() -> None:
        nonlocal errors
        while loop.time() < deadline:
            index = next(counter)
            started = time.perf_counter()
            try:
                if spec.body_factory is not None:
                    response = await client.request(
                        spec.method, spec.path, json=spec.body_factory(index)
                    )
                else:
                    response = await client.request(spec.method, spec.path, params=spec.params)
                elapsed_ms = (time.perf_counter() - started) * 1000
                latencies.append(elapsed_ms)
                status_counts[str(response.status_code)] = (
                    status_counts.get(str(response.status_code), 0) + 1
                )
                if response.status_code not in spec.expected:
                    errors += 1
            except Exception:
                errors += 1
                latencies.append((time.perf_counter() - started) * 1000)

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    wall_start = time.perf_counter()
    await asyncio.gather(*workers)
    wall = time.perf_counter() - wall_start
    requests = len(latencies)
    stats = latency_stats(latencies)
    return {
        "endpoint": spec.name,
        "method": spec.method,
        "path": spec.path,
        "requests": requests,
        "duration_s": round(wall, 3),
        "rps": round(requests / wall, 2) if wall else 0.0,
        **stats,
        "errors": errors,
        "error_rate_pct": round(errors / requests * 100, 3) if requests else 0.0,
        "status_counts": status_counts,
    }


async def bench_latency(
    base_url: str,
    enterprise: dict[str, Any],
    *,
    concurrency: int,
    duration: float,
) -> list[dict[str, Any]]:
    headers = {"X-API-Key": enterprise["api_key"]}
    today = date.today()
    start = today - timedelta(days=29)
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=60.0,
        limits=httpx.Limits(max_connections=concurrency * 3, max_keepalive_connections=concurrency),
    ) as client:
        page = (await client.get("/v1/campaigns", params={"limit": 1})).json()
        if not page["data"]:
            raise RuntimeError("Seed data is missing campaigns for the enterprise partner.")
        campaign_id = page["data"][0]["id"]
        run_id = uuid.uuid4().hex[:8]
        specs = [
            EndpointSpec(
                name="GET /v1/campaigns",
                method="GET",
                path="/v1/campaigns",
                params={"limit": 50},
                expected={200},
            ),
            EndpointSpec(
                name="GET /v1/campaigns/{id}",
                method="GET",
                path=f"/v1/campaigns/{campaign_id}",
                expected={200},
            ),
            EndpointSpec(
                name="GET /v1/reports/metrics",
                method="GET",
                path="/v1/reports/metrics",
                params={
                    "start_date": start.isoformat(),
                    "end_date": today.isoformat(),
                    "group_by": "day",
                },
                expected={200},
            ),
            EndpointSpec(
                name="POST /v1/campaigns",
                method="POST",
                path="/v1/campaigns",
                body_factory=lambda index: {
                    "name": f"Bench Campaign {run_id} {index:06d}",
                    "status": "active",
                    "daily_budget_cents": 5000,
                    "objective": "conversions",
                },
                expected={201},
            ),
        ]

        results: list[dict[str, Any]] = []
        for spec in specs:
            for _ in range(5):
                try:
                    if spec.body_factory is not None:
                        await client.request(spec.method, spec.path, json=spec.body_factory(-1))
                    else:
                        await client.request(spec.method, spec.path, params=spec.params)
                except httpx.HTTPError:
                    pass
            result = await run_load(client, spec, concurrency=concurrency, duration=duration)
            results.append(result)
            print(
                f"  {spec.name}: {result['requests']} req, {result['rps']} rps, "
                f"p95={result['p95_ms']} ms, errors={result['error_rate_pct']}%"
            )
        return results


def reset_bucket(partner: dict[str, Any]) -> None:
    get_redis().delete(f"ratelimit:{partner['tier']}:{partner['partner_id']}")


async def bench_rate_limit(
    base_url: str, free: dict[str, Any], *, requests: int = 140
) -> dict[str, Any]:
    reset_bucket(free)
    headers = {"X-API-Key": free["api_key"]}
    capacity = 2 * int(free["rate_limit_rpm"])
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=60.0,
        limits=httpx.Limits(max_connections=requests + 20, max_keepalive_connections=requests),
    ) as client:
        started = time.perf_counter()
        responses = await asyncio.gather(
            *(client.get("/v1/partners/me", headers=headers) for _ in range(requests)),
            return_exceptions=True,
        )
        window = time.perf_counter() - started

    allowed = 0
    limited = 0
    failed = 0
    retry_after_values: list[int] = []
    retry_after_valid = 0
    envelope_ok = 0
    headers_present = 0
    for response in responses:
        if isinstance(response, BaseException):
            failed += 1
            continue
        if response.status_code == 200:
            allowed += 1
            if all(
                response.headers.get(header)
                for header in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset")
            ):
                headers_present += 1
        elif response.status_code == 429:
            limited += 1
            raw = response.headers.get("Retry-After")
            try:
                value = int(str(raw))
            except (TypeError, ValueError):
                value = 0
            retry_after_values.append(value)
            if value >= 1:
                retry_after_valid += 1
            try:
                if response.json().get("error", {}).get("type") == "rate_limited":
                    envelope_ok += 1
            except ValueError:
                pass
        else:
            failed += 1

    deviation = allowed - capacity
    refill = window * int(free["rate_limit_rpm"]) / 60.0
    return {
        "steady_rpm": int(free["rate_limit_rpm"]),
        "configured_burst_capacity": capacity,
        "requests_fired": requests,
        "allowed": allowed,
        "rate_limited": limited,
        "other_failures": failed,
        "window_seconds": round(window, 3),
        "refill_tokens_during_window": round(refill, 2),
        "absolute_deviation": deviation,
        "percent_deviation": round(deviation / capacity * 100, 2),
        "retry_after_valid_count": retry_after_valid,
        "retry_after_samples": sorted(set(retry_after_values)),
        "rate_limit_headers_present_on_200": headers_present,
        "rate_limited_envelope_count": envelope_ok,
    }


async def _exhaust_free_bucket(base_url: str, free: dict[str, Any], count: int = 140) -> int:
    reset_bucket(free)
    headers = {"X-API-Key": free["api_key"]}
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=60.0,
        limits=httpx.Limits(max_connections=80, max_keepalive_connections=40),
    ) as client:
        responses = await asyncio.gather(
            *(client.get("/v1/partners/me", headers=headers) for _ in range(count)),
            return_exceptions=True,
        )
    return sum(1 for r in responses if not isinstance(r, BaseException) and r.status_code == 200)


async def bench_retry(base_url: str, free: dict[str, Any], *, attempts: int = 5) -> dict[str, Any]:
    reset_bucket(free)
    async with APIPartnerHubClient(
        base_url, api_key=free["api_key"], retry_policy=RetryPolicy(max_retries=0)
    ) as client:
        baseline_latencies = []
        for _ in range(10):
            started = time.perf_counter()
            await client.get_me()
            baseline_latencies.append((time.perf_counter() - started) * 1000)

    exhausted_allowed = await _exhaust_free_bucket(base_url, free)
    await asyncio.sleep(0)

    no_retry_success = 0
    no_retry_latencies: list[float] = []
    async with APIPartnerHubClient(
        base_url, api_key=free["api_key"], retry_policy=RetryPolicy(max_retries=0)
    ) as client:
        for _ in range(attempts):
            started = time.perf_counter()
            try:
                await client.get_me()
                no_retry_success += 1
            except APIPartnerHubError:
                pass
            no_retry_latencies.append((time.perf_counter() - started) * 1000)

    await _exhaust_free_bucket(base_url, free)
    retry_success = 0
    retry_latencies: list[float] = []
    total_retries = 0
    async with APIPartnerHubClient(
        base_url, api_key=free["api_key"], retry_policy=RetryPolicy(max_retries=10)
    ) as client:
        for _ in range(attempts):
            started = time.perf_counter()
            try:
                result = await client.get_me()
                retry_success += 1
                total_retries += max(0, result.attempts - 1)
            except APIPartnerHubError:
                total_retries += max(0, client.last_attempts - 1)
            retry_latencies.append((time.perf_counter() - started) * 1000)

    baseline_mean = statistics.fmean(baseline_latencies)
    retry_mean = statistics.fmean(retry_latencies)
    return {
        "baseline_requests": len(baseline_latencies),
        "baseline_mean_ms": round(baseline_mean, 2),
        "exhaustion_allowed_before_429": exhausted_allowed,
        "requests": attempts,
        "no_retry_success": no_retry_success,
        "no_retry_success_rate_pct": round(no_retry_success / attempts * 100, 2),
        "no_retry_mean_ms": round(statistics.fmean(no_retry_latencies), 2),
        "retry_success": retry_success,
        "retry_success_rate_pct": round(retry_success / attempts * 100, 2),
        "total_retries": total_retries,
        "retry_mean_ms": round(retry_mean, 2),
        "mean_added_latency_ms": round(retry_mean - baseline_mean, 2),
    }


def generate_csv(path: Path, rows: int, run_id: str) -> int:
    invalid = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "status", "daily_budget_cents", "objective"])
        for index in range(1, rows + 1):
            if index % 50 == 0:
                status = "launched"
                invalid += 1
            else:
                status = "active"
            writer.writerow([f"Bulk Bench {run_id} {index:06d}", status, 2500, "awareness"])
    return invalid


async def bench_csv(base_url: str, enterprise: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    headers = {"X-API-Key": enterprise["api_key"]}
    run_id = uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(base_url=base_url, timeout=300.0) as client:
        for rows in (1_000, 10_000):
            with tempfile.NamedTemporaryFile(
                suffix=".csv", prefix=f"bench_{rows}_", delete=False
            ) as handle:
                path = Path(handle.name)
            generated_invalid = generate_csv(path, rows, run_id)
            try:
                with path.open("rb") as handle:
                    files = {"file": (path.name, handle, "text/csv")}
                    started = time.perf_counter()
                    response = await client.post("/v1/bulk/campaigns", headers=headers, files=files)
                    elapsed = time.perf_counter() - started
                payload = response.json()
                results.append(
                    {
                        "rows": rows,
                        "generated_invalid": generated_invalid,
                        "seconds": round(elapsed, 3),
                        "rows_per_sec": round(rows / elapsed, 2),
                        "http_status": response.status_code,
                        "accepted": payload.get("accepted"),
                        "rejected": payload.get("rejected"),
                        "error_count": len(payload.get("errors", [])),
                    }
                )
                print(
                    f"  CSV {rows} rows: {round(rows / elapsed, 2)} rows/s, "
                    f"status={response.status_code}, accepted={payload.get('accepted')}"
                )
            finally:
                path.unlink(missing_ok=True)
    return results


async def bench_auth(
    base_url: str, enterprise: dict[str, Any], *, requests: int = 30
) -> dict[str, Any]:
    token_response = httpx.post(
        f"{base_url}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": enterprise["client_id"],
            "client_secret": enterprise["client_secret"],
        },
        timeout=30.0,
    )
    token_response.raise_for_status()
    token = token_response.json()["access_token"]

    async def measure(headers: dict[str, str]) -> dict[str, Any]:
        latencies: list[float] = []
        errors = 0
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            for _ in range(requests):
                started = time.perf_counter()
                try:
                    response = await client.get("/v1/partners/me", headers=headers)
                    if response.status_code != 200:
                        errors += 1
                except httpx.HTTPError:
                    errors += 1
                latencies.append((time.perf_counter() - started) * 1000)
        stats = latency_stats(latencies)
        return {
            "requests": requests,
            "errors": errors,
            **stats,
        }

    api_key_result = await measure({"X-API-Key": enterprise["api_key"]})
    oauth_result = await measure({"Authorization": f"Bearer {token}"})
    return {
        "endpoint": "GET /v1/partners/me",
        "api_key": api_key_result,
        "oauth": oauth_result,
        "mean_overhead_ms": round(api_key_result["mean_ms"] - oauth_result["mean_ms"], 2),
    }


async def main_async(args: argparse.Namespace) -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    credentials = load_credentials()
    free = credentials["free"]
    enterprise = credentials["enterprise"]
    base_url = f"http://127.0.0.1:{args.port}"

    process: subprocess.Popen[bytes] | None = None
    log_handle = None
    if not args.skip_server:
        process, log_handle = start_server(args.port, args.workers)
        try:
            wait_for_health(base_url)
            await asyncio.sleep(2.0)
        except Exception:
            process.terminate()
            raise

    try:
        environment = collect_environment(
            port=args.port,
            workers=args.workers,
            concurrency=args.concurrency,
            duration=args.duration,
        )
        write_json("raw_environment.json", environment)
        print(
            f"Environment: {environment['machine']}, {environment['cpu_count']} CPUs, "
            f"Python {environment['python_version']}, "
            f"PostgreSQL {environment['postgres_version']}, "
            f"Redis {environment['redis_version']}, {args.workers} workers"
        )

        print("A. Latency and throughput (enterprise tier)")
        latency = await bench_latency(
            base_url,
            enterprise,
            concurrency=args.concurrency,
            duration=args.duration,
        )
        write_json("raw_latency.json", latency)

        print("B. Rate-limit enforcement (free tier)")
        rate_limit = await bench_rate_limit(base_url, free, requests=args.rate_requests)
        write_json("raw_rate_limit.json", rate_limit)
        print(
            f"  allowed={rate_limit['allowed']} "
            f"configured={rate_limit['configured_burst_capacity']} "
            f"deviation={rate_limit['absolute_deviation']} ({rate_limit['percent_deviation']}%)"
        )

        print("C. SDK retry recovery (free tier)")
        retry = await bench_retry(base_url, free)
        write_json("raw_retry.json", retry)
        print(
            f"  success without retries={retry['no_retry_success_rate_pct']}% "
            f"with retries={retry['retry_success_rate_pct']}%"
        )

        print("E. Auth-mode overhead (enterprise tier)")
        auth = await bench_auth(base_url, enterprise)
        write_json("raw_auth.json", auth)
        print(
            f"  api_key mean={auth['api_key']['mean_ms']} ms, "
            f"oauth mean={auth['oauth']['mean_ms']} ms"
        )

        print("D. CSV bulk import throughput (enterprise tier)")
        csv_results = await bench_csv(base_url, enterprise)
        write_json("raw_csv.json", csv_results)

        print("Benchmark complete. Run `python -m bench.analyze` to build RESULTS.md.")
        return 0
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if log_handle is not None:
            log_handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--rate-requests", type=int, default=140)
    parser.add_argument(
        "--skip-server",
        action="store_true",
        help="Use an already-running server at --port instead of starting one.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
